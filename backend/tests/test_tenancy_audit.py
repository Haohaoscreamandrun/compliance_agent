"""Unit tests for Task 11.4: company isolation for Copilot, Toolset, EntityAlias, and ChemicalEvaluation."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.api.routes_copilot import _compute_audit_cache_key
from app.config import Settings
from app.db.models import AuditReportCache, CompanyUser, PUBLIC_DEMO_COMPANY_ID
from app.db.session import get_db_session
from app.main import create_app
from app.services.tools import CopilotToolset


async def test_compute_audit_cache_key_differs_by_company() -> None:
    session = AsyncMock()
    session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[]),
        scalar_one_or_none=MagicMock(return_value=None),
    )
    key_a = await _compute_audit_cache_key(session, "農藥進03225", set(), company_id="comp_a")
    key_b = await _compute_audit_cache_key(session, "農藥進03225", set(), company_id="comp_b")
    assert key_a != key_b


async def test_copilot_toolset_scopes_document_queries_by_company() -> None:
    session = AsyncMock()
    session.execute.return_value = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
    toolset = CopilotToolset(
        session=session,
        http=AsyncMock(),
        moa_api_base_url="https://example.invalid",
        company_id="comp_a",
    )
    await toolset.get_baseline_documents("農藥進03225")
    sql1 = str(session.execute.call_args.args[0])
    assert "documents.company_id" in sql1

    await toolset.search_official_letters("農藥進03225", "美氟綜")
    sql2 = str(session.execute.call_args.args[0])
    assert "documents.company_id" in sql2


async def test_tenancy_gates_on_copilot_aliases_and_registry_writes(tmp_path: Path) -> None:
    app = create_app(
        Settings(_env_file=None, storage_dir=tmp_path, require_cf_access=True),
        init_db=False,
    )
    session = AsyncMock()
    session.add = MagicMock()
    app.dependency_overrides[get_db_session] = lambda: session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Unauthenticated GET /api/copilot/audit/農藥進99001 (public_demo) succeeds when cached
        demo_report = AuditReportCache(
            company_id=PUBLIC_DEMO_COMPANY_ID,
            reg_no="農藥進99001",
            cache_key="seed_demo_99001",
            report_json={
                "reg_no": "農藥進99001",
                "product_name_zh": "示範殺菌劑",
                "product_name_en": "DEMOFUNGIN",
                "overall_status": "compliant",
                "executive_summary_zh": "ok",
                "executive_summary_en": "ok",
                "checklist_items": [],
            },
            created_at=datetime.now(timezone.utc),
        )
        session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=demo_report))
        demo_resp = await client.get("/api/copilot/audit/農藥進99001")
        assert demo_resp.status_code == 200
        assert demo_resp.json()["from_cache"] is True

        # Unauthenticated GET /api/copilot/audit/農藥進03225 (private permit) -> 401
        priv_resp = await client.get("/api/copilot/audit/農藥進03225")
        assert priv_resp.status_code == 401

        # Un-allowlisted user on /api/entity-aliases and /api/registry/.../aphia/evaluation -> 403
        session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        assert (
            await client.get("/api/entity-aliases", headers={"x-auth-user-email": "stranger@gmail.com"})
        ).status_code == 403
        assert (
            await client.post(
                "/api/registry/農藥進03225/aphia/evaluation",
                json={"submitted_date": date(2026, 1, 1).isoformat()},
                headers={"x-auth-user-email": "stranger@gmail.com"},
            )
        ).status_code == 403
        assert (
            await client.post(
                "/api/registry/aphia/sync",
                headers={"x-auth-user-email": "stranger@gmail.com"},
            )
        ).status_code == 403


async def test_public_demo_cached_audit_always_reads_public_demo_for_default_user(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(_env_file=None, storage_dir=tmp_path, require_cf_access=True),
        init_db=False,
    )
    session = AsyncMock()
    default_user = CompanyUser(email="owner@example.com", company_id="default", role="admin")
    demo_report = AuditReportCache(
        company_id=PUBLIC_DEMO_COMPANY_ID,
        reg_no="農藥進99001",
        cache_key="fresh_demo_99001",
        report_json={
            "reg_no": "農藥進99001",
            "product_name_zh": "示範殺菌劑",
            "product_name_en": "DEMOFUNGIN",
            "overall_status": "pending_moa_approval",
            "executive_summary_zh": "fresh",
            "executive_summary_en": "fresh",
            "checklist_items": [],
        },
        created_at=datetime.now(timezone.utc),
    )
    session.execute.side_effect = [
        MagicMock(scalar_one_or_none=MagicMock(return_value=default_user)),
        MagicMock(scalar_one_or_none=MagicMock(return_value=demo_report)),
    ]
    app.dependency_overrides[get_db_session] = lambda: session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/copilot/audit/農藥進99001",
            headers={"x-auth-user-email": "owner@example.com"},
        )
    assert resp.status_code == 200
    assert resp.json()["overall_status"] == "pending_moa_approval"
    cache_stmt = session.execute.call_args_list[1].args[0]
    compiled = cache_stmt.compile()
    assert PUBLIC_DEMO_COMPANY_ID in compiled.params.values()
    assert "default" not in compiled.params.values()
