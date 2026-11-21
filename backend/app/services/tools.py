# cSpell:words httpx sqlalchemy
"""Four deterministic/SQL tools for Phase 1 of the Two-Phase Compliance Copilot."""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import timezone
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, MoaRegistry
from app.schemas.extraction import (
    MsdsExtraction,
    NotarizedCertExtraction,
    OfficialLetterExtraction,
    ProductLabelExtraction,
)
from app.services.cascade_diff import (
    CascadeDiffFinding,
    normalize_reg_no,
    run_cascade_diff,
    score_matching_cert_product,
)
from app.services.moa_client import MoaNotFoundError, MoaUnavailableError, get_registry_snapshot


def _msds_version_sort_key(doc: Document) -> tuple[str, str]:
    """Sort MSDSs by `issued_version` (normalized prefix/numbers), then UTC `uploaded_at`."""
    raw_ver = ""
    extracted = doc.extracted_data or {}
    iv = extracted.get("issued_version")
    if isinstance(iv, dict):
        raw_ver = str(iv.get("value") or iv.get("verbatim_text") or "").strip()
    # shortcut: assumes all MSDSs for one permit use comparable version formats (either vX.Y or ISO dates).
    stripped = re.sub(r"^(?:version|ver|rev|v)\.?\s*", "", raw_ver.casefold())
    normalized_nums = re.sub(r"\d+", lambda m: m.group(0).zfill(6), stripped)
    uploaded = (
        doc.uploaded_at.astimezone(timezone.utc).isoformat()
        if doc.uploaded_at
        else ""
    )
    return (normalized_nums, uploaded)


class CopilotToolset:
    """Stateful toolset binding DB session, HTTP client, and MOA endpoint for one audit run."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        http: httpx.AsyncClient,
        moa_api_base_url: str,
    ) -> None:
        self.session = session
        self.http = http
        self.moa_api_base_url = moa_api_base_url

        self._moa: MoaRegistry | None = None
        self.moa_stale: bool = False
        self.msds_doc_id_used: str | None = None
        self.warnings: list[str] = []
        self.last_findings: list[CascadeDiffFinding] = []

        self._label: tuple[str, ProductLabelExtraction] | None = None
        self._msds: tuple[str, MsdsExtraction] | None = None
        self._cert: tuple[str, NotarizedCertExtraction] | None = None
        self._extra_certs: list[tuple[str, NotarizedCertExtraction]] = []
        self._cert_candidates: list[tuple[str, NotarizedCertExtraction, bool]] = []
        self._letters: dict[str, OfficialLetterExtraction] = {}

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    @property
    def has_moa(self) -> bool:
        return self._moa is not None

    @property
    def has_label(self) -> bool:
        return self._label is not None

    @property
    def moa_chinese_name(self) -> str | None:
        return self._moa.chinese_name if self._moa else None

    @property
    def moa_english_name(self) -> str | None:
        return self._moa.english_name if self._moa else None

    @property
    def label_common_name(self) -> str | None:
        if self._label and self._label[1].common_name:
            return str(self._label[1].common_name.value)
        return None

    async def lookup_moa_registry(self, reg_no: str) -> dict[str, Any]:
        """Tool 1: Fetch live Taiwan MOA EIR027 record on-call (with PostgreSQL snapshot fallback)."""
        self._moa = None
        self.moa_stale = False
        canon_reg = normalize_reg_no(reg_no)
        try:
            res = await get_registry_snapshot(
                self.session, self.http, self.moa_api_base_url, canon_reg
            )
            self._moa = res.snapshot
            self.moa_stale = res.stale
            if res.stale:
                self._warn(
                    f"MOA EIR027 API was unreachable; used cached snapshot from {res.snapshot.fetched_at.isoformat()}."
                )
            return {
                "found": True,
                "stale": res.stale,
                "reg_no": res.snapshot.reg_no,
                "chinese_name": res.snapshot.chinese_name,
                "english_name": res.snapshot.english_name,
                "company_name": res.snapshot.company_name,
                "foreign_manufacturer": res.snapshot.foreign_manufacturer,
                "formulation": res.snapshot.formulation,
                "content_pct": res.snapshot.content_pct,
                "valid_until_roc": res.snapshot.valid_until_roc,
                "revocation_status": res.snapshot.revocation_status,
            }
        except (MoaNotFoundError, ValueError) as exc:
            self._warn(str(exc))
            return {"found": False, "reg_no": canon_reg, "error": str(exc)}
        except MoaUnavailableError as exc:
            self._warn(str(exc))
            return {"found": False, "reg_no": canon_reg, "error": str(exc), "unavailable": True}

    async def get_baseline_documents(self, reg_no: str) -> dict[str, Any]:
        """Tool 2: Load uploaded Label, latest MSDS (by issued_version), and Change Cert for `reg_no`."""
        self._label = None
        self._msds = None
        self._cert = None
        self._extra_certs = []
        self._cert_candidates = []
        self.msds_doc_id_used = None

        canon_reg = normalize_reg_no(reg_no)
        # Order by is_active_baseline DESC, uploaded_at DESC (latest upload date beats reg_no == canon_reg)
        stmt = (
            select(Document)
            .where(
                Document.doc_type.in_(("label", "msds", "change_cert")),
                or_(
                    Document.reg_no == canon_reg,
                    and_(Document.doc_type == "change_cert", Document.reg_no.is_(None)),
                ),
            )
            .order_by(
                Document.is_active_baseline.desc(),
                Document.uploaded_at.desc(),
            )
            .limit(100)
        )
        docs = list((await self.session.execute(stmt)).scalars().all())

        labels = [d for d in docs if d.doc_type == "label" and d.reg_no == canon_reg]
        msdss = [d for d in docs if d.doc_type == "msds" and d.reg_no == canon_reg]
        certs = [d for d in docs if d.doc_type == "change_cert"]

        if labels:
            doc = labels[0]
            self._label = (doc.doc_id, ProductLabelExtraction.model_validate(doc.extracted_data))
        else:
            self._warn(f"No baseline product label uploaded for '{canon_reg}'.")

        skipped_msds: list[str] = []
        if msdss:
            msdss_sorted = sorted(msdss, key=_msds_version_sort_key, reverse=True)
            latest_msds = msdss_sorted[0]
            self.msds_doc_id_used = latest_msds.doc_id
            self._msds = (latest_msds.doc_id, MsdsExtraction.model_validate(latest_msds.extracted_data))
            skipped_msds = [d.doc_id for d in msdss_sorted[1:]]
            if skipped_msds:
                self._warn(
                    f"Selected latest MSDS '{latest_msds.doc_id}' by issued_version; ignored older MSDS(s): {skipped_msds}."
                )

        for doc in certs:
            try:
                parsed = NotarizedCertExtraction.model_validate(doc.extracted_data)
            except ValidationError:
                self._warn(f"Skipped invalid change_cert payload for doc_id='{doc.doc_id}'.")
                continue
            self._cert_candidates.append((doc.doc_id, parsed, doc.reg_no == canon_reg))
        self._resolve_certs(canon_reg)

        return {
            "reg_no": canon_reg,
            "label_doc_id": self._label[0] if self._label else None,
            "label_common_name": self.label_common_name,
            "msds_doc_id": self._msds[0] if self._msds else None,
            "skipped_msds_doc_ids": skipped_msds,
            "cert_doc_id": self._cert[0] if self._cert else None,
        }

    async def search_official_letters(
        self, reg_no: str, common_name: str | None = None
    ) -> dict[str, Any]:
        """Tool 3: Find uploaded Official Letters (公文) matching `reg_no`, `common_name`, or multi-permit."""
        self._letters.clear()
        canon_reg = normalize_reg_no(reg_no)
        filters = [Document.reg_no == canon_reg, Document.reg_no.is_(None)]
        if common_name and common_name.strip():
            filters.append(Document.common_name == common_name.strip())

        stmt = (
            select(Document)
            .where(Document.doc_type == "official_letter", or_(*filters))
            .order_by(Document.reg_no.is_(None).asc(), Document.uploaded_at.desc())
            .limit(50)
        )
        docs = list((await self.session.execute(stmt)).scalars().all())
        matched_ids: list[str] = []
        for doc in docs:
            try:
                self._letters[doc.doc_id] = OfficialLetterExtraction.model_validate(doc.extracted_data)
                matched_ids.append(doc.doc_id)
            except ValidationError:
                self._warn(f"Skipped invalid official letter payload for doc_id='{doc.doc_id}'.")

        return {"reg_no": canon_reg, "candidate_letter_doc_ids": matched_ids}

    def _resolve_certs(self, canon_reg: str) -> None:
        """Pick the latest matching cert per subtype (LoA + MoC + Free Sale + other), skipping LoO."""
        self._cert = None
        self._extra_certs = []
        if not self._cert_candidates:
            return
        label_model = self._label[1] if self._label else None
        chosen_by_subtype: dict[str, tuple[str, NotarizedCertExtraction]] = {}
        for doc_id, parsed_cert, is_explicit in self._cert_candidates:
            subtype = parsed_cert.cert_subtype.value if parsed_cert.cert_subtype else "other"
            if subtype == "loo":
                continue
            _, score = score_matching_cert_product(parsed_cert, canon_reg, label_model, self._moa)
            if not is_explicit and score <= 0:
                continue
            if subtype not in chosen_by_subtype:
                chosen_by_subtype[subtype] = (doc_id, parsed_cert)

        selected = list(chosen_by_subtype.values())
        if selected:
            self._cert = selected[0]
            self._extra_certs = selected[1:]

    async def run_compliance_diff(self, reg_no: str) -> dict[str, Any]:
        """Tool 4: Run deterministic `run_cascade_diff` across all loaded sources for `reg_no`."""
        canon_reg = normalize_reg_no(reg_no)
        self._resolve_certs(canon_reg)
        findings = run_cascade_diff(
            reg_no=canon_reg,
            label=self._label[1] if self._label else None,
            label_doc_id=self._label[0] if self._label else None,
            msds=self._msds[1] if self._msds else None,
            msds_doc_id=self._msds[0] if self._msds else None,
            change_cert=self._cert[1] if self._cert else None,
            cert_doc_id=self._cert[0] if self._cert else None,
            extra_certs=self._extra_certs,
            moa=self._moa,
            official_letters=list(self._letters.items()),
        )
        self.last_findings = findings
        return {
            "reg_no": canon_reg,
            "finding_count": len(findings),
            "findings": [asdict(f) for f in findings],
        }
