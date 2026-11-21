# cSpell:words httpx genai
"""Two-Phase Compliance Copilot audit routes."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from google.genai import errors as genai_errors
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.session import get_db_session
from app.schemas.report import ComplianceAuditReport
from app.services.cascade_diff import validate_reg_no
from app.services.copilot import run_compliance_audit
from app.services.parser import GeminiParseError
from app.services.tools import CopilotToolset

router = APIRouter(prefix="/api/copilot", tags=["copilot"])


@router.post("/audit/{reg_no}", response_model=ComplianceAuditReport)
async def audit_permit(
    reg_no: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> ComplianceAuditReport:
    """Run the Two-Phase Compliance Copilot audit for Taiwan pesticide permit `reg_no`."""
    try:
        canon_reg = validate_reg_no(reg_no)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client = request.app.state.genai_client
    if client is None:
        raise HTTPException(status_code=503, detail="Gemini client is not configured.")

    settings: Settings = request.app.state.settings
    http: httpx.AsyncClient = request.app.state.http_client
    toolset = CopilotToolset(
        session=session,
        http=http,
        moa_api_base_url=settings.moa_api_base_url,
    )

    try:
        return await run_compliance_audit(
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
