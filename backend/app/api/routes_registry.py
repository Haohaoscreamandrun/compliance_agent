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
