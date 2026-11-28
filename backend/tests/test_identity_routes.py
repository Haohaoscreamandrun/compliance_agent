"""Unit tests for Task 11.5: /api/me and /api/access-requests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx

from app.config import Settings
from app.db.models import CompanyUser
from app.db.session import get_db_session
from app.main import create_app


async def test_me_and_access_request_flow(tmp_path: Path) -> None:
    app = create_app(
        Settings(_env_file=None, storage_dir=tmp_path, require_cf_access=True),
        init_db=False,
    )
    session = AsyncMock()
    session.add = MagicMock()
    app.dependency_overrides[get_db_session] = lambda: session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unauthenticated /api/me -> authenticated=False
        r_unauth = await client.get("/api/me")
        assert r_unauth.status_code == 200
        assert r_unauth.json()["authenticated"] is False

        # 2. Allowlisted user /api/me -> company_id + company_name + role
        cu = CompanyUser(email="alice@compa.com", company_id="comp_a", role="member")
        session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=cu)),
            MagicMock(scalar_one_or_none=MagicMock(return_value="Comp A Corp")),
        ]
        r_member = await client.get("/api/me", headers={"x-auth-user-email": "alice@compa.com"})
        assert r_member.status_code == 200
        assert r_member.json()["company_id"] == "comp_a"
        assert r_member.json()["company_name"] == "Comp A Corp"

        # 3. Un-allowlisted user submits Join Company request -> 201
        session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # not in company_users
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # no pending request
        ]

        def _fake_refresh(obj):
            obj.id = 7
            obj.created_at = datetime(2026, 11, 25, tzinfo=timezone.utc)

        session.refresh.side_effect = _fake_refresh
        r_req = await client.post(
            "/api/access-requests",
            json={"requested_company_name": "Demo AgroChem", "note": "RA team"},
            headers={"x-auth-user-email": "new@gmail.com"},
        )
        assert r_req.status_code == 201
        assert r_req.json()["email"] == "new@gmail.com"
        assert r_req.json()["status"] == "pending"


async def test_admin_onboarding_requires_admin_and_assigns_company(tmp_path: Path) -> None:
    app = create_app(
        Settings(_env_file=None, storage_dir=tmp_path, require_cf_access=True),
        init_db=False,
    )
    session = AsyncMock()
    session.add = MagicMock()
    app.dependency_overrides[get_db_session] = lambda: session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Member role -> 403 on admin endpoints
        member = CompanyUser(email="alice@compa.com", company_id="comp_a", role="member")
        session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=member))
        assert (
            await client.get("/api/admin/access-requests", headers={"x-auth-user-email": "alice@compa.com"})
        ).status_code == 403
        assert (
            await client.post(
                "/api/admin/company-users",
                json={"email": "new@gmail.com", "company_id": "comp_a"},
                headers={"x-auth-user-email": "alice@compa.com"},
            )
        ).status_code == 403

        # 2. Admin cannot assign to public_demo -> 400
        admin = CompanyUser(email="admin@compa.com", company_id="default", role="admin")
        session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=admin)),
        ]
        assert (
            await client.post(
                "/api/admin/company-users",
                json={"email": "new@gmail.com", "company_id": "public_demo"},
                headers={"x-auth-user-email": "admin@compa.com"},
            )
        ).status_code == 400

        # 3. Admin assigns user to comp_acme -> 200 and updates pending requests
        session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=admin)),
            MagicMock(),  # upsert Company
            MagicMock(),  # upsert CompanyUser
            MagicMock(),  # update CompanyAccessRequest
        ]
        ok = await client.post(
            "/api/admin/company-users",
            json={
                "email": "New@Gmail.com",
                "company_id": "comp_acme",
                "company_name": "Acme AgroChem",
                "role": "member",
            },
            headers={"x-auth-user-email": "admin@compa.com"},
        )
        assert ok.status_code == 200
        assert ok.json()["email"] == "new@gmail.com"
        assert ok.json()["company_id"] == "comp_acme"
        session.commit.assert_awaited()


async def test_auth_login_redirect_safety(tmp_path: Path) -> None:
    app = create_app(Settings(_env_file=None, gemini_api_key="test", storage_dir=tmp_path), init_db=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        r_ok = await client.get("/api/auth/login?next=/documents%3Freg%3D1")
        assert r_ok.status_code == 302
        assert r_ok.headers["location"] == "/documents?reg=1"

        r_evil = await client.get("/api/auth/login?next=//evil.example/phish")
        assert r_evil.status_code == 302
        assert r_evil.headers["location"] == "/"
