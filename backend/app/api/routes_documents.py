# cSpell:words webp pymupdf genai
"""Document upload, listing, baseline selection, and WebP page image routes."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from google.genai import errors as genai_errors
from pydantic import BaseModel, ValidationError
from sqlalchemy import exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import VALID_DOC_TYPES, Document, DocumentClause, DocumentPage, MoaRegistry
from app.db.session import get_db_session
from app.schemas.extraction import NotarizedCertExtraction, ProductLabelExtraction
from app.services.cascade_diff import score_matching_cert_product, validate_reg_no
from app.services.parser import GeminiParseError, extract_clauses_for_db, parse_document_pages
from app.services.preprocessor import preprocess_upload_in_memory, validate_doc_id

router = APIRouter(prefix="/api/documents", tags=["documents"])

DocType = Literal["label", "msds", "change_cert", "official_letter"]


class DocumentSummary(BaseModel):
    doc_id: str
    filename: str
    doc_type: str
    reg_no: str | None = None
    volume_tag: str = ""
    common_name: str | None = None
    is_active_baseline: bool
    page_count: int
    uploaded_at: datetime


class DocumentUploadResponse(DocumentSummary):
    model_used: str
    used_fallback: bool
    extracted_data: dict[str, Any]


class BaselineToggleRequest(BaseModel):
    is_active_baseline: bool = True


def _validate_doc_type(doc_type: str) -> str:
    cleaned = doc_type.strip()
    if cleaned not in VALID_DOC_TYPES:
        raise HTTPException(
            status_code=400, detail=f"doc_type must be one of {VALID_DOC_TYPES}, got {doc_type!r}"
        )
    return cleaned


def _resolve_optional_reg_no(raw_reg: str | None) -> str | None:
    if raw_reg is None or not raw_reg.strip():
        return None
    return validate_reg_no(raw_reg)


class DocumentExistsResponse(BaseModel):
    exists: bool
    doc_id: str | None = None


@router.get("/exists", response_model=DocumentExistsResponse)
async def check_document_exists(
    filename: str = Query(..., min_length=1),
    doc_type: DocType = Query(...),
    reg_no: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> DocumentExistsResponse:
    """Check whether a filename already exists for `(filename, doc_type, reg_no)`."""
    safe_name = Path(filename.replace("\\", "/")).name[:256]
    validated_type = _validate_doc_type(doc_type)
    try:
        resolved_reg = _resolve_optional_reg_no(reg_no)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    stmt = (
        select(Document.doc_id)
        .where(
            Document.filename == safe_name,
            Document.doc_type == validated_type,
            Document.reg_no.is_not_distinct_from(resolved_reg),
        )
        .limit(1)
    )
    existing_id = (await session.execute(stmt)).scalar_one_or_none()
    return DocumentExistsResponse(
        exists=existing_id is not None,
        doc_id=str(existing_id) if existing_id is not None else None,
    )


@router.post("/upload", response_model=DocumentUploadResponse, status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    doc_type: DocType = Form(...),
    reg_no: str | None = Form(default=None),
    volume_tag: str | None = Form(
        default=None,
        description="Optional packaging/volume tag (e.g. '1kg', '2kg') to keep multiple label baselines per reg_no.",
    ),
    manual_notes: str | None = Form(
        default=None,
        description="Optional manual notes (recommended for 'other' unformatted certificates).",
    ),
    force_reparse: bool = Form(
        default=False,
        description="When true, bypass content_sha256 cache, re-parse with Gemini, and promote to baseline.",
    ),
    session: AsyncSession = Depends(get_db_session),
) -> DocumentUploadResponse:
    """In-RAM preprocess -> Gemini constrained extraction -> PostgreSQL JSONB persistence."""
    doc_type = _validate_doc_type(doc_type)
    try:
        explicit_reg = _resolve_optional_reg_no(reg_no)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client = request.app.state.genai_client
    if client is None:
        raise HTTPException(status_code=503, detail="Gemini client is not configured.")

    settings: Settings = request.app.state.settings
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    raw_bytes = await file.read(max_bytes + 1)
    if len(raw_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Uploaded file exceeds {settings.max_upload_size_mb} MB limit.",
        )

    content_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    requested_vol = (volume_tag or "").strip()[:64] if doc_type == "label" else ""
    if not force_reparse:
        cached_stmt = select(Document).where(
            Document.content_sha256 == content_sha256,
            Document.doc_type == doc_type,
            Document.reg_no.is_not_distinct_from(explicit_reg),
        )
        if requested_vol:
            cached_stmt = cached_stmt.where(Document.volume_tag == requested_vol)
        cached_doc = (await session.execute(cached_stmt.limit(1))).scalar_one_or_none()
        if isinstance(cached_doc, Document) and cached_doc.content_sha256 == content_sha256:
            return DocumentUploadResponse(
                doc_id=cached_doc.doc_id,
                filename=cached_doc.filename,
                doc_type=cached_doc.doc_type,
                reg_no=cached_doc.reg_no,
                volume_tag=cached_doc.volume_tag or "",
                common_name=cached_doc.common_name,
                is_active_baseline=cached_doc.is_active_baseline,
                page_count=cached_doc.page_count,
                uploaded_at=cached_doc.uploaded_at,
                model_used="db-cache",
                used_fallback=False,
                extracted_data=cached_doc.extracted_data,
            )

    doc_id = f"{doc_type}_{uuid4().hex[:12]}"
    raw_name = (file.filename or f"{doc_id}.bin").replace("\\", "/")
    filename = Path(raw_name).name or f"{doc_id}.bin"

    try:
        pages = await asyncio.to_thread(
            preprocess_upload_in_memory,
            raw_bytes,
            filename=filename,
            doc_id=doc_id,
            storage_dir=settings.storage_dir,
            max_edge_px=settings.page_image_max_edge_px,
            webp_quality=settings.page_image_webp_quality,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    committed = False
    try:
        try:
            parse_res = await parse_document_pages(
                client=client,
                model_name=settings.gemini_parse_model,
                doc_type=doc_type,
                pages=pages,
                fallback_models=settings.gemini_parse_fallback_models,
            )
        except (GeminiParseError, genai_errors.APIError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        extracted_dict = parse_res.extraction.model_dump()
        extracted_reg_gf = getattr(parse_res.extraction, "reg_no", None)
        extracted_reg: str | None = None
        if extracted_reg_gf is not None:
            try:
                extracted_reg = _resolve_optional_reg_no(str(extracted_reg_gf.value))
            except ValueError:
                extracted_reg = None

        if explicit_reg is not None and extracted_reg is not None and explicit_reg != extracted_reg:
            raise HTTPException(
                status_code=400,
                detail=f"Uploaded document permit '{extracted_reg}' does not match provided reg_no '{explicit_reg}'.",
            )
        resolved_reg = explicit_reg or extracted_reg
        cert_sub_gf = getattr(parse_res.extraction, "cert_subtype", None)
        if doc_type == "change_cert" and cert_sub_gf and cert_sub_gf.value == "other" and not resolved_reg:
            raise HTTPException(
                status_code=400,
                detail="reg_no must be provided manually when uploading an 'other' / unformatted certificate.",
            )
        if manual_notes and manual_notes.strip():
            extracted_dict["manual_notes"] = manual_notes.strip()

        extracted_cn_gf = getattr(parse_res.extraction, "common_name", None)
        if extracted_cn_gf is not None:
            resolved_cn: str | None = str(extracted_cn_gf.value).strip()[:128]
        elif hasattr(parse_res.extraction, "products"):
            names = list(
                dict.fromkeys(
                    str(p.common_name.value).strip()
                    for p in parse_res.extraction.products
                    if getattr(p, "common_name", None) is not None
                )
            )
            resolved_cn = (", ".join(names)[:128] or None) if names else None
        else:
            resolved_cn = None

        resolved_vol = ""
        if doc_type == "label":
            raw_vol = (volume_tag or "").strip()
            if not raw_vol:
                net_vol_gf = getattr(parse_res.extraction, "net_contents_or_volume", None)
                if net_vol_gf is not None and getattr(net_vol_gf, "value", None):
                    raw_vol = str(net_vol_gf.value).strip()
            resolved_vol = raw_vol[:64]

        safe_filename = filename[:256]
        prior_match = (
            await session.execute(
                select(Document.doc_id)
                .where(
                    Document.filename == safe_filename,
                    Document.doc_type == doc_type,
                    Document.reg_no.is_not_distinct_from(resolved_reg),
                    Document.volume_tag == resolved_vol,
                    Document.common_name.is_not_distinct_from(resolved_cn),
                    Document.page_count == len(pages),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        is_active_baseline = force_reparse or (prior_match is not None)

        try:
            if is_active_baseline:
                if resolved_reg:
                    await session.execute(
                        update(Document)
                        .where(
                            Document.reg_no == resolved_reg,
                            Document.doc_type == doc_type,
                            Document.volume_tag == resolved_vol,
                            Document.is_active_baseline.is_(True),
                        )
                        .values(is_active_baseline=False)
                    )
                else:
                    await session.execute(
                        update(Document)
                        .where(
                            Document.filename == safe_filename,
                            Document.doc_type == doc_type,
                            Document.reg_no.is_(None),
                            Document.common_name.is_not_distinct_from(resolved_cn),
                            Document.page_count == len(pages),
                            Document.is_active_baseline.is_(True),
                        )
                        .values(is_active_baseline=False)
                    )

            doc_row = Document(
                doc_id=doc_id,
                filename=safe_filename,
                doc_type=doc_type,
                reg_no=resolved_reg,
                volume_tag=resolved_vol,
                content_sha256=content_sha256,
                common_name=resolved_cn,
                is_active_baseline=is_active_baseline,
                page_count=len(pages),
                extracted_data=extracted_dict,
            )
            session.add(doc_row)
            for p in pages:
                session.add(
                    DocumentPage(
                        doc_id=doc_id,
                        page_number=p.page_number,
                        image_rel_path=p.image_rel_path,
                        width_px=p.width_px,
                        height_px=p.height_px,
                    )
                )
            for clause_dict in extract_clauses_for_db(
                doc_id=doc_id,
                doc_type=doc_type,
                reg_no=resolved_reg,
                common_name=resolved_cn,
                extraction=parse_res.extraction,
            ):
                session.add(DocumentClause(**clause_dict))

            await session.commit()
            await session.refresh(doc_row)
            committed = True
        except Exception:
            await session.rollback()
            raise
    finally:
        if not committed:
            await asyncio.to_thread(
                shutil.rmtree, settings.storage_dir / "pages" / doc_id, ignore_errors=True
            )

    return DocumentUploadResponse(
        doc_id=doc_row.doc_id,
        filename=doc_row.filename,
        doc_type=doc_row.doc_type,
        reg_no=doc_row.reg_no,
        volume_tag=doc_row.volume_tag,
        common_name=doc_row.common_name,
        is_active_baseline=doc_row.is_active_baseline,
        page_count=doc_row.page_count,
        uploaded_at=doc_row.uploaded_at,
        model_used=parse_res.model_used,
        used_fallback=parse_res.used_fallback,
        extracted_data=doc_row.extracted_data,
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=list[DocumentSummary])
async def list_documents(
    reg_no: str | None = Query(default=None),
    doc_type: DocType | None = Query(default=None),
    is_active_baseline: bool | None = Query(default=None),
    include_shared_certs: bool = Query(
        default=False,
        description="When true with reg_no, also include matching multi-product (reg_no=NULL) change_certs.",
    ),
    common_name: str | None = Query(
        default=None, description="Filter by Chinese or English active-ingredient common name."
    ),
    product_internal_code: str | None = Query(
        default=None, description="Filter by internal product code (e.g. 'BAS 175 01 F')."
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> list[DocumentSummary]:
    """List uploaded regulatory documents, filtered by permit, doc_type, baseline, common_name, or product code."""
    stmt = select(Document).order_by(Document.uploaded_at.desc())
    canon_reg: str | None = None
    if reg_no is not None and reg_no != "":
        try:
            canon_reg = validate_reg_no(reg_no)
            stmt = stmt.where(Document.reg_no == canon_reg)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if doc_type is not None and doc_type != "":
        stmt = stmt.where(Document.doc_type == _validate_doc_type(doc_type))
    if is_active_baseline is not None:
        stmt = stmt.where(Document.is_active_baseline.is_(is_active_baseline))
    if common_name is not None and common_name.strip():
        pattern = f"%{_escape_like(common_name.strip())}%"
        clause_match = exists(
            select(DocumentClause.id).where(
                DocumentClause.doc_id == Document.doc_id,
                DocumentClause.section_id.ilike("%common_name%"),
                or_(
                    DocumentClause.normalized_value.ilike(pattern, escape="\\"),
                    DocumentClause.verbatim_text.ilike(pattern, escape="\\"),
                ),
            )
        )
        stmt = stmt.where(or_(Document.common_name.ilike(pattern, escape="\\"), clause_match))
    if product_internal_code is not None and product_internal_code.strip():
        code_pattern = f"%{_escape_like(product_internal_code.strip())}%"
        stmt = stmt.where(
            exists(
                select(DocumentClause.id).where(
                    DocumentClause.doc_id == Document.doc_id,
                    DocumentClause.section_id.ilike("%product_internal_code%"),
                    or_(
                        DocumentClause.normalized_value.ilike(code_pattern, escape="\\"),
                        DocumentClause.verbatim_text.ilike(code_pattern, escape="\\"),
                    ),
                )
            )
        )

    rows = list((await session.execute(stmt.offset(offset).limit(limit))).scalars().all())
    if include_shared_certs and canon_reg is not None:
        seen_ids = {r.doc_id for r in rows}
        reg_docs = (
            await session.execute(
                select(Document)
                .where(Document.reg_no == canon_reg)
                .order_by(Document.is_active_baseline.desc(), Document.uploaded_at.desc())
                .limit(100)
            )
        ).scalars().all()

        if not any(r.doc_type == "label" for r in rows):
            seen_vols: set[str] = set()
            for d in reg_docs:
                if d.doc_type == "label" and (d.volume_tag or "") not in seen_vols:
                    seen_vols.add(d.volume_tag or "")
                    if d.doc_id not in seen_ids:
                        rows.append(d)
                        seen_ids.add(d.doc_id)

        for fallback_type in ("msds", "change_cert"):
            if not any(r.doc_type == fallback_type for r in rows):
                for d in reg_docs:
                    if d.doc_type == fallback_type and d.doc_id not in seen_ids:
                        rows.append(d)
                        seen_ids.add(d.doc_id)
                        break

        label_model: ProductLabelExtraction | None = None
        for r in rows:
            if r.doc_type == "label":
                try:
                    label_model = ProductLabelExtraction.model_validate(r.extracted_data)
                    break
                except ValidationError:
                    pass
        moa = (
            await session.execute(select(MoaRegistry).where(MoaRegistry.reg_no == canon_reg))
        ).scalar_one_or_none()
        shared_certs = (
            await session.execute(
                select(Document)
                .where(Document.doc_type == "change_cert", Document.reg_no.is_(None))
                .order_by(Document.uploaded_at.desc())
                .limit(50)
            )
        ).scalars().all()
        for cert_doc in shared_certs:
            if cert_doc.doc_id in seen_ids:
                continue
            try:
                parsed_cert = NotarizedCertExtraction.model_validate(cert_doc.extracted_data)
            except ValidationError:
                continue
            _, score = score_matching_cert_product(parsed_cert, canon_reg, label_model, moa)
            if score > 0:
                rows.append(cert_doc)
                seen_ids.add(cert_doc.doc_id)

    return [
        DocumentSummary(
            doc_id=r.doc_id,
            filename=r.filename,
            doc_type=r.doc_type,
            reg_no=r.reg_no,
            volume_tag=r.volume_tag or "",
            common_name=r.common_name,
            is_active_baseline=r.is_active_baseline,
            page_count=r.page_count,
            uploaded_at=r.uploaded_at,
        )
        for r in rows
    ]


@router.patch("/{doc_id}/baseline", response_model=DocumentSummary)
async def set_document_baseline(
    doc_id: str,
    body: BaselineToggleRequest,
    session: AsyncSession = Depends(get_db_session),
) -> DocumentSummary:
    """Mark or unmark a document as the active baseline for its `(reg_no, doc_type)`."""
    try:
        safe_id = validate_doc_id(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    doc = (await session.execute(select(Document).where(Document.doc_id == safe_id))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document '{safe_id}' not found.")
    if body.is_active_baseline and not doc.reg_no:
        raise HTTPException(
            status_code=400,
            detail="Cannot set is_active_baseline=True on a document without reg_no.",
        )

    if body.is_active_baseline and doc.reg_no:
        await session.execute(
            update(Document)
            .where(
                Document.reg_no == doc.reg_no,
                Document.doc_type == doc.doc_type,
                Document.volume_tag == doc.volume_tag,
                Document.doc_id != safe_id,
                Document.is_active_baseline.is_(True),
            )
            .values(is_active_baseline=False)
        )
    doc.is_active_baseline = body.is_active_baseline
    await session.commit()
    await session.refresh(doc)

    return DocumentSummary(
        doc_id=doc.doc_id,
        filename=doc.filename,
        doc_type=doc.doc_type,
        reg_no=doc.reg_no,
        common_name=doc.common_name,
        is_active_baseline=doc.is_active_baseline,
        page_count=doc.page_count,
        uploaded_at=doc.uploaded_at,
    )


@router.get("/{doc_id}/pages/{page_number}/image")
async def get_document_page_image(
    doc_id: str,
    page_number: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> FileResponse:
    """Serve normalized `.webp` page image for `<svg viewBox="0 0 1000 1000">` overlay."""
    try:
        safe_id = validate_doc_id(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if page_number < 1:
        raise HTTPException(status_code=400, detail="page_number must be >= 1.")

    page_row = (
        await session.execute(
            select(DocumentPage).where(
                DocumentPage.doc_id == safe_id,
                DocumentPage.page_number == page_number,
            )
        )
    ).scalar_one_or_none()
    if page_row is None:
        raise HTTPException(status_code=404, detail=f"Page {page_number} of '{safe_id}' not found.")

    storage_root = request.app.state.settings.storage_dir.resolve()
    doc_dir = (storage_root / "pages" / safe_id).resolve()
    image_path = (storage_root / page_row.image_rel_path).resolve()
    if not image_path.is_relative_to(doc_dir) or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Page image file missing on disk.")

    return FileResponse(
        path=image_path,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=3600, immutable"},
    )
