"""FastAPI authentication and company-tenancy dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DEFAULT_COMPANY_ID, CompanyUser
from app.db.session import get_db_session


@dataclass(frozen=True)
class CompanyUserContext:
    """Verified user email and explicit company membership."""

    email: str
    company_id: str
    role: str


def _is_cf_access_required(request: Request) -> bool:
    settings = getattr(request.app.state, "settings", None)
    return bool(getattr(settings, "require_cf_access", False))


def get_verified_email(request: Request) -> str | None:
    """Return normalized verified email injected by the Cloudflare Worker edge."""
    raw = request.headers.get("x-auth-user-email", "").strip().lower()
    return raw or None


async def get_optional_company_user(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> CompanyUserContext | None:
    """Resolve explicit CompanyUser membership when authenticated, or local default in dev mode."""
    email = get_verified_email(request)
    if email is None:
        if _is_cf_access_required(request):
            return None
        return CompanyUserContext(
            email="local@localhost",
            company_id=DEFAULT_COMPANY_ID,
            role="admin",
        )

    row = (
        await session.execute(select(CompanyUser).where(CompanyUser.email == email))
    ).scalar_one_or_none()
    if row is None:
        return None
    return CompanyUserContext(email=row.email, company_id=row.company_id, role=row.role)


async def get_current_company_user(
    request: Request,
    user: CompanyUserContext | None = Depends(get_optional_company_user),
) -> CompanyUserContext:
    """Require an authenticated user with an explicit company_users allowlist row."""
    if user is not None:
        return user
    if get_verified_email(request) is None and _is_cf_access_required(request):
        raise HTTPException(status_code=401, detail="Authentication required.")
    raise HTTPException(
        status_code=403,
        detail="Your account is not assigned to a company yet. Submit a Join Company request.",
    )


async def require_admin_user(
    user: CompanyUserContext = Depends(get_current_company_user),
) -> CompanyUserContext:
    """Require an authenticated company admin."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    return user
