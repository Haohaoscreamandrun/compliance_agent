# cSpell:words httpx
"""On-call Taiwan MOA EIR027 registry lookup routes."""

from __future__ import annotations

from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
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
    keywords: list[str]
    forbid_matches: list[AphiaRecordItem]
    assessment_matches: list[AphiaRecordItem]
    announcements: list[AphiaRecordItem]
    stale: bool
    fetched_at: datetime


@router.get("/{reg_no}/aphia", response_model=AphiaLookupResponse)
async def lookup_aphia_by_reg_no(
    reg_no: str,
    request: Request,
    refresh: bool = False,
    session: AsyncSession = Depends(get_db_session),
) -> AphiaLookupResponse:
    """Lookup APHIA banned/restricted/carcinogen lists, assessments & announcements for `reg_no`."""
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
    return AphiaLookupResponse(
        reg_no=canon_reg,
        keywords=aphia["keywords"],
        forbid_matches=[AphiaRecordItem(**r) for r in aphia["forbid_matches"]],
        assessment_matches=[AphiaRecordItem(**r) for r in aphia["assessment_matches"]],
        announcements=[AphiaRecordItem(**r) for r in aphia["announcements"]],
        stale=aphia["stale"],
        fetched_at=aphia["fetched_at"],
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
