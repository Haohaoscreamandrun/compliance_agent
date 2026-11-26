# cSpell:words httpx genai
"""Two-Phase Compliance Copilot audit routes with PostgreSQL audit report caching."""

from __future__ import annotations

import hashlib
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from google.genai import errors as genai_errors
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CompanyUserContext,
    get_current_company_user,
    get_optional_company_user,
    get_verified_email,
)
from app.config import Settings
from app.db.models import (
    DEFAULT_COMPANY_ID,
    PUBLIC_DEMO_COMPANY_ID,
    PUBLIC_DEMO_REG_NO,
    AuditReportCache,
    Document,
    EntityAlias,
    MoaRegistry,
    _utc_now,
)
from app.db.session import get_db_session
from app.schemas.report import ComplianceAuditReport
from app.services.aphia_client import extract_ingredient_tokens, lookup_aphia_for_tokens
from app.services.cascade_diff import validate_reg_no
from app.services.copilot import run_compliance_audit
from app.services.parser import GeminiParseError
from app.services.tools import CopilotToolset

router = APIRouter(prefix="/api/copilot", tags=["copilot"])


@router.get("/audit/{reg_no}", response_model=ComplianceAuditReport)
async def get_cached_audit_report(
    reg_no: str,
    request: Request,
    user: CompanyUserContext | None = Depends(get_optional_company_user),
    session: AsyncSession = Depends(get_db_session),
) -> ComplianceAuditReport:
    """Read-only endpoint returning the latest PostgreSQL-cached audit report for `reg_no`."""
    try:
        canon_reg = validate_reg_no(reg_no)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if user is None:
        if canon_reg != PUBLIC_DEMO_REG_NO:
            if get_verified_email(request) is None:
                raise HTTPException(status_code=401, detail="Authentication required.")
            raise HTTPException(status_code=403, detail="Your account is not assigned to a company yet.")
        target_company_id = PUBLIC_DEMO_COMPANY_ID
    else:
        target_company_id = user.company_id

    cached = (
        await session.execute(
            select(AuditReportCache)
            .where(
                AuditReportCache.company_id == target_company_id,
                AuditReportCache.reg_no == canon_reg,
            )
            .order_by(AuditReportCache.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not isinstance(cached, AuditReportCache) and canon_reg == PUBLIC_DEMO_REG_NO and target_company_id != PUBLIC_DEMO_COMPANY_ID:
        cached = (
            await session.execute(
                select(AuditReportCache)
                .where(
                    AuditReportCache.company_id == PUBLIC_DEMO_COMPANY_ID,
                    AuditReportCache.reg_no == canon_reg,
                )
                .order_by(AuditReportCache.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if not isinstance(cached, AuditReportCache):
        raise HTTPException(status_code=404, detail=f"No cached audit report found for '{canon_reg}'.")
    report = ComplianceAuditReport.model_validate(cached.report_json)
    report.from_cache = True
    return report


async def _compute_audit_cache_key(
    session: AsyncSession,
    canon_reg: str,
    confirmed_aliases: set[tuple[str, str]],
    *,
    company_id: str = DEFAULT_COMPANY_ID,
) -> str:
    """Build a deterministic SHA-256 key from company, participating docs, baselines, MOA snapshot, and aliases."""
    doc_res = await session.execute(
        select(Document.doc_id, Document.is_active_baseline)
        .where(
            Document.company_id == company_id,
            or_(Document.reg_no == canon_reg, Document.reg_no.is_(None)),
        )
        .order_by(Document.doc_id.asc())
    )
    raw_docs = doc_res.all()
    doc_fp = (
        [(str(r[0]), bool(r[1])) for r in raw_docs]
        if isinstance(raw_docs, (list, tuple))
        else []
    )
    moa_res = await session.execute(select(MoaRegistry).where(MoaRegistry.reg_no == canon_reg))
    moa_row = moa_res.scalar_one_or_none()
    moa_fp = (
        {
            "chinese_name": moa_row.chinese_name,
            "english_name": moa_row.english_name,
            "company_name": moa_row.company_name,
            "foreign_manufacturer": moa_row.foreign_manufacturer,
            "formulation": moa_row.formulation,
            "content_pct": moa_row.content_pct,
            "valid_until_roc": moa_row.valid_until_roc,
            "revocation_status": moa_row.revocation_status,
        }
        if isinstance(moa_row, MoaRegistry)
        else None
    )
    payload = json.dumps(
        {
            "company_id": company_id,
            "reg_no": canon_reg,
            "docs": doc_fp,
            "moa": moa_fp,
            "aliases": sorted(confirmed_aliases),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@router.post("/audit/{reg_no}", response_model=ComplianceAuditReport)
async def audit_permit(
    reg_no: str,
    request: Request,
    force_refresh: bool = Query(default=False, description="Bypass PostgreSQL audit cache."),
    user: CompanyUserContext = Depends(get_current_company_user),
    session: AsyncSession = Depends(get_db_session),
) -> ComplianceAuditReport:
    """Run (or return cached) Two-Phase Compliance Copilot audit for Taiwan pesticide permit `reg_no`."""
    try:
        canon_reg = validate_reg_no(reg_no)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    alias_res = await session.execute(
        select(EntityAlias.zh_name, EntityAlias.en_name).where(
            EntityAlias.company_id == user.company_id,
            EntityAlias.confirmed.is_(True),
        )
    )
    raw_aliases = alias_res.all()
    confirmed_aliases = (
        {(str(r[0]), str(r[1])) for r in raw_aliases}
        if isinstance(raw_aliases, (list, tuple))
        else set()
    )

    cache_key = await _compute_audit_cache_key(
        session, canon_reg, confirmed_aliases, company_id=user.company_id
    )
    if not force_refresh:
        cached = (
            await session.execute(
                select(AuditReportCache).where(
                    AuditReportCache.company_id == user.company_id,
                    AuditReportCache.cache_key == cache_key,
                )
            )
        ).scalar_one_or_none()
        if isinstance(cached, AuditReportCache):
            cached_report = ComplianceAuditReport.model_validate(cached.report_json)
            cached_report.from_cache = True
            return cached_report

    client = request.app.state.genai_client
    if client is None:
        raise HTTPException(status_code=503, detail="Gemini client is not configured.")

    settings: Settings = request.app.state.settings
    http: httpx.AsyncClient = request.app.state.http_client
    toolset = CopilotToolset(
        session=session,
        http=http,
        moa_api_base_url=settings.moa_api_base_url,
        confirmed_aliases=confirmed_aliases,
        company_id=user.company_id,
    )

    try:
        report = await run_compliance_audit(
            client=client,
            toolset=toolset,
            reg_no=canon_reg,
            model_name=settings.gemini_copilot_model,
            fallback_models=settings.gemini_parse_fallback_models,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (GeminiParseError, genai_errors.APIError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        tokens = extract_ingredient_tokens(report.product_name_zh, report.product_name_en)
        if tokens:
            aphia = await lookup_aphia_for_tokens(session, http, tokens)
            for hit in aphia.get("carcinogen_matches", [])[:3]:
                msg = f"APHIA 可能致癌農藥提醒: {hit['title']}"
                if msg not in report.audit_warnings:
                    report.audit_warnings.append(msg)
    except Exception:
        # Best-effort APHIA enrichment must never stall or fail Copilot audits
        pass

    cache_key = await _compute_audit_cache_key(
        session, canon_reg, confirmed_aliases, company_id=user.company_id
    )
    upsert_stmt = (
        pg_insert(AuditReportCache)
        .values(
            company_id=user.company_id,
            reg_no=canon_reg,
            cache_key=cache_key,
            report_json=report.model_dump(),
            created_at=_utc_now(),
        )
        .on_conflict_do_update(
            index_elements=["cache_key"],
            set_={
                "report_json": report.model_dump(),
                "created_at": _utc_now(),
            },
        )
    )
    await session.execute(upsert_stmt)
    await session.commit()
    return report
