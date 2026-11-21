# cSpell:words webp pymupdf genai
"""Document upload, listing, baseline selection, and WebP page image routes."""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from google.genai import errors as genai_errors
from pydantic import BaseModel
from sqlalchemy import exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import VALID_DOC_TYPES, Document, DocumentClause, DocumentPage
from app.db.session import get_db_session
from app.services.cascade_diff import validate_reg_no
from app.services.parser import GeminiParseError, extract_clauses_for_db, parse_document_pages
from app.services.preprocessor import preprocess_upload_in_memory, validate_doc_id

router = APIRouter(prefix="/api/documents", tags=["documents"])

DocType = Literal["label", "msds", "change_cert", "official_letter"]


class DocumentSummary(BaseModel):
    doc_id: str
    filename: str
    doc_type: str
    reg_no: str | None = None
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


@router.post("/upload", response_model=DocumentUploadResponse, status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    doc_type: DocType = Form(...),
    reg_no: str | None = Form(default=None),
    manual_notes: str | None = Form(
        default=None,
        description="Optional manual notes (recommended for 'other' unformatted certificates).",
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

        safe_filename = filename[:256]
        prior_match = (
            await session.execute(
                select(Document.doc_id)
                .where(
                    Document.filename == safe_filename,
                    Document.doc_type == doc_type,
                    Document.reg_no.is_not_distinct_from(resolved_reg),
                    Document.common_name.is_not_distinct_from(resolved_cn),
                    Document.page_count == len(pages),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        is_active_baseline = prior_match is not None

        try:
            if is_active_baseline:
                if resolved_reg:
                    await session.execute(
                        update(Document)
                        .where(
                            Document.reg_no == resolved_reg,
                            Document.doc_type == doc_type,
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
    """List uploaded regulatory documents, filtered by permit, doc_type, common_name, or product code."""
    stmt = select(Document).order_by(Document.uploaded_at.desc())
    if reg_no is not None and reg_no != "":
        try:
            stmt = stmt.where(Document.reg_no == validate_reg_no(reg_no))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if doc_type is not None and doc_type != "":
        stmt = stmt.where(Document.doc_type == _validate_doc_type(doc_type))
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

    rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return [
        DocumentSummary(
            doc_id=r.doc_id,
            filename=r.filename,
            doc_type=r.doc_type,
            reg_no=r.reg_no,
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
