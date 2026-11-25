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
