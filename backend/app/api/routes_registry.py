# cSpell:words httpx
"""On-call Taiwan MOA EIR027 registry lookup routes."""

from __future__ import annotations

from datetime import date, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import ChemicalEvaluation, _utc_now
from app.db.session import get_db_session
from app.services.aphia_client import (
    extract_ingredient_tokens,
    lookup_aphia_for_tokens,
    sync_aphia_catalog,
)
from app.services.cascade_diff import validate_reg_no
from app.services.moa_client import (
    MoaNotFoundError,
    MoaUnavailableError,
    get_registry_snapshot,
)

router = APIRouter(prefix="/api/registry", tags=["registry"])


class RegistryLookupResponse(BaseModel):
    reg_no: str
    permit_type: str
    permit_number: str
    chinese_name: str | None = None
    english_name: str | None = None
    pesticide_code: str | None = None
    brand_name: str | None = None
    company_name: str | None = None
    foreign_manufacturer: str | None = None
    formulation: str | None = None
    content_pct: str | None = None
    valid_until_roc: str | None = None
    revocation_status: str | None = None
    stale: bool
    fetched_at: datetime


class AphiaRecordItem(BaseModel):
    source_kind: str
    source_key: str
    title: str
    common_name: str | None = None
    published_date: str | None = None
    detail_url: str | None = None


class AphiaLookupResponse(BaseModel):
    reg_no: str
    chinese_name: str | None = None
    keywords: list[str]
    carcinogen_matches: list[AphiaRecordItem]
    assessment_matches: list[AphiaRecordItem]
    latest_pre_notice: AphiaRecordItem | None = None
    evaluation_submitted_date: str | None = None
    stale: bool
    fetched_at: datetime


class EvaluationSubmissionRequest(BaseModel):
    submitted_date: date
    notes: str | None = None


class EvaluationSubmissionResponse(BaseModel):
    reg_no: str
    common_name: str
    submitted_date: str
    notes: str | None = None
    updated_at: datetime


@router.get("/{reg_no}/aphia", response_model=AphiaLookupResponse)
async def lookup_aphia_by_reg_no(
    reg_no: str,
    request: Request,
    refresh: bool = False,
    session: AsyncSession = Depends(get_db_session),
) -> AphiaLookupResponse:
    """Lookup APHIA carcinogen PDF, 15-year assessment (code=2), and latest 預先通知公告."""
    try:
        canon_reg = validate_reg_no(reg_no)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    settings: Settings = request.app.state.settings
    http: httpx.AsyncClient = request.app.state.http_client
    try:
        moa_res = await get_registry_snapshot(session, http, settings.moa_api_base_url, canon_reg)
    except MoaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MoaUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    snap = moa_res.snapshot
    tokens = extract_ingredient_tokens(snap.chinese_name, snap.english_name)
    aphia = await lookup_aphia_for_tokens(session, http, tokens, force_refresh=refresh)

    common_key = (snap.chinese_name or snap.english_name or "").strip()
    eval_row = None
    if common_key:
        eval_row = (
            await session.execute(
                select(ChemicalEvaluation).where(ChemicalEvaluation.common_name == common_key)
            )
        ).scalar_one_or_none()

    latest_notice = (
        AphiaRecordItem(**aphia["latest_pre_notice"])
        if aphia.get("latest_pre_notice")
        else None
    )
    return AphiaLookupResponse(
        reg_no=canon_reg,
        chinese_name=snap.chinese_name,
        keywords=aphia["keywords"],
        carcinogen_matches=[AphiaRecordItem(**r) for r in aphia["carcinogen_matches"]],
        assessment_matches=[AphiaRecordItem(**r) for r in aphia["assessment_matches"]],
        latest_pre_notice=latest_notice,
        evaluation_submitted_date=eval_row.submitted_date if eval_row else None,
        stale=aphia["stale"],
        fetched_at=aphia["fetched_at"],
    )


@router.post("/{reg_no}/aphia/evaluation", response_model=EvaluationSubmissionResponse)
async def save_evaluation_submission_date(
    reg_no: str,
    payload: EvaluationSubmissionRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> EvaluationSubmissionResponse:
    """Save 15-year evaluation report submission date keyed by the chemical's common name."""
    try:
        canon_reg = validate_reg_no(reg_no)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    settings: Settings = request.app.state.settings
    http: httpx.AsyncClient = request.app.state.http_client
    try:
        moa_res = await get_registry_snapshot(session, http, settings.moa_api_base_url, canon_reg)
    except MoaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MoaUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    snap = moa_res.snapshot
    common_name = (snap.chinese_name or snap.english_name or "").strip()
    if not common_name:
        raise HTTPException(status_code=422, detail="MOA record has no common name.")

    now = _utc_now()
    submitted_iso = payload.submitted_date.isoformat()
    stmt = (
        pg_insert(ChemicalEvaluation)
        .values(
            common_name=common_name,
            submitted_date=submitted_iso,
            notes=payload.notes,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[ChemicalEvaluation.common_name],
            set_={
                "submitted_date": submitted_iso,
                "notes": payload.notes,
                "updated_at": now,
            },
        )
    )
    await session.execute(stmt)
    await session.commit()
    return EvaluationSubmissionResponse(
        reg_no=canon_reg,
        common_name=common_name,
        submitted_date=submitted_iso,
        notes=payload.notes,
        updated_at=now,
    )


@router.post("/aphia/sync")
async def sync_aphia_records(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, int]:
    """Sync APHIA static/recent tables into PostgreSQL aphia_records."""
    http: httpx.AsyncClient = request.app.state.http_client
    synced = await sync_aphia_catalog(session, http)
    return {"synced": synced}


@router.get("/{reg_no}", response_model=RegistryLookupResponse)
async def lookup_registry(
    reg_no: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> RegistryLookupResponse:
    """Fetch live Taiwan MOA EIR027 record on-call (or cached PostgreSQL snapshot)."""
    try:
        canon_reg = validate_reg_no(reg_no)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    settings: Settings = request.app.state.settings
    http: httpx.AsyncClient = request.app.state.http_client
    try:
        result = await get_registry_snapshot(session, http, settings.moa_api_base_url, canon_reg)
    except MoaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MoaUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    snap = result.snapshot
    return RegistryLookupResponse(
        reg_no=snap.reg_no,
        permit_type=snap.permit_type,
        permit_number=snap.permit_number,
        chinese_name=snap.chinese_name,
        english_name=snap.english_name,
        pesticide_code=snap.pesticide_code,
        brand_name=snap.brand_name,
        company_name=snap.company_name,
        foreign_manufacturer=snap.foreign_manufacturer,
        formulation=snap.formulation,
        content_pct=snap.content_pct,
        valid_until_roc=snap.valid_until_roc,
        revocation_status=snap.revocation_status,
        stale=result.stale,
        fetched_at=snap.fetched_at,
    )
