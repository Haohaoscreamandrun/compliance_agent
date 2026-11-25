"""Current user identity (/api/me) and company access application routes."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CompanyUserContext,
    get_optional_company_user,
    get_verified_email,
)
from app.db.models import Company, CompanyAccessRequest
from app.db.session import get_db_session

router = APIRouter(prefix="/api", tags=["identity"])


class PendingAccessRequestOut(BaseModel):
    id: int
    requested_company_name: str
    status: str
    created_at: datetime


class MeResponse(BaseModel):
    authenticated: bool
    email: str | None = None
    company_id: str | None = None
    company_name: str | None = None
    role: str | None = None
    pending_request: PendingAccessRequestOut | None = None


class AccessRequestCreate(BaseModel):
    requested_company_name: str = Field(..., min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("requested_company_name")
    @classmethod
    def _non_blank_company(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("requested_company_name must not be blank.")
        return cleaned


class AccessRequestOut(BaseModel):
    id: int
    email: str
    requested_company_name: str
    note: str | None = None
    status: str
    assigned_company_id: str | None = None
    created_at: datetime


@router.get("/me", response_model=MeResponse)
async def get_me(
    request: Request,
    user: CompanyUserContext | None = Depends(get_optional_company_user),
    session: AsyncSession = Depends(get_db_session),
) -> MeResponse:
    """Return current verified user identity, company membership, or pending join request."""
    if user is not None:
        company_name = (
            await session.execute(
                select(Company.name).where(Company.company_id == user.company_id)
            )
        ).scalar_one_or_none()
        return MeResponse(
            authenticated=True,
            email=user.email,
            company_id=user.company_id,
            company_name=company_name or user.company_id,
            role=user.role,
        )

    email = get_verified_email(request)
    if email is None:
        return MeResponse(authenticated=False)

    pending = (
        await session.execute(
            select(CompanyAccessRequest)
            .where(
                CompanyAccessRequest.email == email,
                CompanyAccessRequest.status == "pending",
            )
            .order_by(CompanyAccessRequest.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    pending_out = (
        PendingAccessRequestOut(
            id=pending.id,
            requested_company_name=pending.requested_company_name,
            status=pending.status,
            created_at=pending.created_at,
        )
        if pending is not None
        else None
    )
    return MeResponse(authenticated=True, email=email, pending_request=pending_out)


@router.post("/access-requests", response_model=AccessRequestOut, status_code=201)
async def create_access_request(
    body: AccessRequestCreate,
    request: Request,
    user: CompanyUserContext | None = Depends(get_optional_company_user),
    session: AsyncSession = Depends(get_db_session),
) -> AccessRequestOut:
    """Submit a Join Company application for an authenticated user not yet assigned to a company."""
    email = user.email if user is not None else get_verified_email(request)
    if email is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if user is not None:
        raise HTTPException(
            status_code=409,
            detail=f"User is already assigned to company '{user.company_id}'.",
        )

    existing_pending = (
        await session.execute(
            select(CompanyAccessRequest).where(
                CompanyAccessRequest.email == email,
                CompanyAccessRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if existing_pending is not None:
        raise HTTPException(
            status_code=409,
            detail="A pending company access request already exists for this email.",
        )

    row = CompanyAccessRequest(
        email=email,
        requested_company_name=body.requested_company_name,
        note=body.note.strip() if body.note and body.note.strip() else None,
        status="pending",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return AccessRequestOut(
        id=row.id,
        email=row.email,
        requested_company_name=row.requested_company_name,
        note=row.note,
        status=row.status,
        assigned_company_id=row.assigned_company_id,
        created_at=row.created_at,
    )
