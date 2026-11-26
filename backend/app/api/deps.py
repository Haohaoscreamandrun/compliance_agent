"""FastAPI authentication and company-tenancy dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DEFAULT_COMPANY_ID, PUBLIC_DEMO_COMPANY_ID, CompanyUser
from app.db.session import get_db_session

WORKSPACE_HEADER = "x-workspace-company-id"


@dataclass(frozen=True)
class CompanyUserContext:
    """Verified user email and explicit company membership."""

    email: str
    company_id: str
    role: str
    can_manage_public_demo: bool = False


def _is_cf_access_required(request: Request) -> bool:
    settings = getattr(request.app.state, "settings", None)
    return bool(getattr(settings, "require_cf_access", False))


def get_verified_email(request: Request) -> str | None:
    """Return normalized verified email injected by the Cloudflare Worker edge."""
    raw = request.headers.get("x-auth-user-email", "").strip().lower()
    return raw or None


def _apply_workspace_override(
    request: Request,
    base_user: CompanyUserContext | None,
) -> CompanyUserContext | None:
    requested = request.headers.get(WORKSPACE_HEADER, "").strip()
    if not requested:
        return base_user
    if base_user is None or not base_user.can_manage_public_demo:
        raise HTTPException(
            status_code=403,
            detail="Only default workspace admins may switch into the public demo workspace.",
        )
    if requested not in (DEFAULT_COMPANY_ID, PUBLIC_DEMO_COMPANY_ID):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported workspace override '{requested}'; only '{DEFAULT_COMPANY_ID}' and '{PUBLIC_DEMO_COMPANY_ID}' are allowed.",
        )
    return CompanyUserContext(
        email=base_user.email,
        company_id=requested,
        role=base_user.role,
        can_manage_public_demo=True,
    )


async def get_optional_company_user(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> CompanyUserContext | None:
    """Resolve explicit CompanyUser membership when authenticated, or local default in dev mode."""
    email = get_verified_email(request)
    if email is None:
        if _is_cf_access_required(request):
            return _apply_workspace_override(request, None)
        base_user = CompanyUserContext(
            email="local@localhost",
            company_id=DEFAULT_COMPANY_ID,
            role="admin",
            can_manage_public_demo=True,
        )
        return _apply_workspace_override(request, base_user)

    row = (
        await session.execute(select(CompanyUser).where(CompanyUser.email == email))
    ).scalar_one_or_none()
    if row is None:
        return _apply_workspace_override(request, None)
    base_user = CompanyUserContext(
        email=row.email,
        company_id=row.company_id,
        role=row.role,
        can_manage_public_demo=(row.company_id == DEFAULT_COMPANY_ID and row.role == "admin"),
    )
    return _apply_workspace_override(request, base_user)


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
