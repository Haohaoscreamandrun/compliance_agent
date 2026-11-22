"""Unit tests for Component 7: PageStorage (local/R2), postgres_ssl, and GET /api/copilot/audit."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.config import Settings
from app.db.models import AuditReportCache
from app.db.session import build_async_engine, get_db_session
from app.main import create_app
from app.schemas.report import ComplianceAuditReport
from app.services.preprocessor import ProcessedPage
from app.services.storage import PageStorage


def test_page_storage_local_and_r2_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local_settings = Settings(
        postgres_password="pw",
        storage_dir=tmp_path / "storage",
        secrets_dir=tmp_path / "secrets",
    )
    local_storage = PageStorage(local_settings)
    assert local_storage.uses_r2 is False

    rel = "pages/lbl_1/page_1.webp"
    p = tmp_path / "storage" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"WEBP1")
    assert local_storage.read_page("lbl_1", rel) == b"WEBP1"
    local_storage.delete_doc_pages("lbl_1")
    assert not p.exists()

    # R2 mode
    r2_settings = Settings(
        postgres_password="pw",
        storage_dir=tmp_path / "storage",
        secrets_dir=tmp_path / "secrets",
        r2_endpoint_url="https://acct.r2.cloudflarestorage.com",
        r2_bucket="compliance-pages",
        r2_access_key_id="ak",
        r2_secret_access_key="sk",
    )
    r2_storage = PageStorage(r2_settings)
    assert r2_storage.uses_r2 is True

    fake_s3 = MagicMock()
    fake_s3.get_object.return_value = {"Body": BytesIO(b"R2WEBP")}
    fake_s3.list_objects_v2.return_value = {"Contents": [{"Key": rel}]}
    monkeypatch.setattr(r2_storage, "_s3_client", lambda: fake_s3)

    r2_storage.save_pages(
        "lbl_1",
        [ProcessedPage(page_number=1, image_rel_path=rel, width_px=100, height_px=100, webp_bytes=b"R2WEBP")],
    )
    fake_s3.put_object.assert_called_once()
    assert r2_storage.read_page("lbl_1", rel) == b"R2WEBP"
    r2_storage.delete_doc_pages("lbl_1")
    fake_s3.delete_objects.assert_called_once()


def test_postgres_ssl_flag_configures_async_engine(tmp_path: Path) -> None:
    settings = Settings(
        postgres_password="pw",
        postgres_ssl=True,
        storage_dir=tmp_path / "storage",
        secrets_dir=tmp_path / "secrets",
    )
    engine = build_async_engine(settings)
    assert engine is not None


@pytest.mark.asyncio
async def test_get_cached_audit_report_readonly_endpoint(tmp_path: Path) -> None:
    settings = Settings(
        postgres_password="pw",
        gemini_api_key="",
        storage_dir=tmp_path / "storage",
        secrets_dir=tmp_path / "secrets",
    )
    app = create_app(settings, init_db=False)
    cached_report = ComplianceAuditReport(
        reg_no="農藥進03225",
        overall_status="pending_moa_approval",
        executive_summary_zh="公開展示快取報告。",
        executive_summary_en="Public demo cached report.",
    )
    cache_row = AuditReportCache(
        id=1,
        reg_no="農藥進03225",
        cache_key="seed_demo_03225",
        report_json=cached_report.model_dump(),
    )
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=cache_row))

    async def _override_db():
        yield mock_session

    app.dependency_overrides[get_db_session] = _override_db

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/copilot/audit/農藥進03225")
            assert resp.status_code == 200
            assert resp.json()["from_cache"] is True
            assert resp.json()["reg_no"] == "農藥進03225"
