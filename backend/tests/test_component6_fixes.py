"""Regression tests for Component 6: confirmed entity aliases, multi-volume baselines, and prompts."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from PIL import Image

from app.config import Settings
from app.db.models import AuditReportCache, Document
from app.db.session import get_db_session
from app.main import create_app
from app.prompts.extraction_prompts import EXTRACTION_PROMPTS
from app.schemas.extraction import NotarizedCertExtraction, ProductLabelExtraction
from app.schemas.grounded import GroundedField
from app.schemas.report import ComplianceAuditReport
from app.services.cascade_diff import run_cascade_diff
from app.services.parser import ParseResult


def _gf(val: str, page: int = 1) -> GroundedField[str]:
    return GroundedField[str](verbatim_text=val, value=val, page=page, box_2d=[10, 10, 50, 50])


def test_confirmed_entity_alias_suppresses_bilingual_holder_diff() -> None:
    label = ProductLabelExtraction(
        reg_no=_gf("農藥進00569"),
        local_permit_holder_name=_gf("台灣巴斯夫股份有限公司"),
    )
    cert = NotarizedCertExtraction(
        cert_subtype=_gf("loa"),
        authorized_entity_name=_gf("BASF Taiwan Ltd."),
    )
    unconfirmed = run_cascade_diff(
        reg_no="農藥進00569",
        label=label,
        label_doc_id="lbl_1",
        change_cert=cert,
        cert_doc_id="cert_1",
    )
    assert any(f.category == "entity_contact_update" for f in unconfirmed)

    confirmed = run_cascade_diff(
        reg_no="農藥進00569",
        label=label,
        label_doc_id="lbl_1",
        change_cert=cert,
        cert_doc_id="cert_1",
        confirmed_aliases={("台灣巴斯夫股份有限公司", "BASF Taiwan Ltd.")},
    )
    assert not any(f.category == "entity_contact_update" for f in confirmed)


def test_prompts_enforce_chinese_label_formulation_and_loa_issuing_agency() -> None:
    assert "Chinese" in EXTRACTION_PROMPTS["label"]
    assert "NEVER the LoA recipient" in EXTRACTION_PROMPTS["change_cert"]


@pytest.mark.asyncio
async def test_entity_alias_routes_reject_blank_and_upsert(tmp_path: Path) -> None:
    settings = Settings(
        postgres_password="test_password",
        gemini_api_key="test-key",
        storage_dir=tmp_path / "storage",
        secrets_dir=tmp_path / "secrets",
    )
    app = create_app(settings, init_db=False)
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    async def _override_db():
        yield mock_session

    app.dependency_overrides[get_db_session] = _override_db

    def _fake_refresh(obj):
        if getattr(obj, "id", None) is None:
            obj.id = 1

    mock_session.refresh.side_effect = _fake_refresh
    mock_session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            bad = await client.post(
                "/api/entity-aliases",
                json={"zh_name": "   ", "en_name": "BASF Taiwan Ltd.", "confirmed": True},
            )
            assert bad.status_code == 422

            ok = await client.post(
                "/api/entity-aliases",
                json={"zh_name": "台灣巴斯夫股份有限公司", "en_name": "BASF Taiwan Ltd.", "confirmed": True},
            )
            assert ok.status_code == 200
            assert ok.json()["confirmed"] is True


@pytest.mark.asyncio
async def test_audit_endpoint_returns_cached_report_without_calling_gemini(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        postgres_password="test_password",
        gemini_api_key="",  # Even without Gemini configured, cache hit succeeds
        storage_dir=tmp_path / "storage",
        secrets_dir=tmp_path / "secrets",
    )
    app = create_app(settings, init_db=False)
    cached_report = ComplianceAuditReport(
        reg_no="農藥進00569",
        overall_status="compliant",
        executive_summary_zh="已快取之合規報告。",
        executive_summary_en="Cached compliance report.",
    )
    cache_row = AuditReportCache(
        id=1,
        reg_no="農藥進00569",
        cache_key="abc123",
        report_json=cached_report.model_dump(),
    )

    mock_session = AsyncMock()
    mock_session.execute.side_effect = [
        MagicMock(all=MagicMock(return_value=[])),
        MagicMock(all=MagicMock(return_value=[("lbl_1", True)])),
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        MagicMock(scalar_one_or_none=MagicMock(return_value=cache_row)),
    ]

    async def _override_db():
        yield mock_session

    app.dependency_overrides[get_db_session] = _override_db

    async def _boom(**kwargs):
        raise AssertionError("Gemini audit should not be called on cache hit")

    monkeypatch.setattr("app.api.routes_copilot.run_compliance_audit", _boom)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/copilot/audit/農藥進00569")
            assert resp.status_code == 200
            assert resp.json()["executive_summary_zh"] == "已快取之合規報告。"
            assert resp.json()["from_cache"] is True


@pytest.mark.asyncio
async def test_document_exists_upload_cache_and_shared_certs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    buf = BytesIO()
    Image.new("RGB", (64, 64), color=(255, 255, 255)).save(buf, format="WEBP")
    webp_bytes = buf.getvalue()
    sha = hashlib.sha256(webp_bytes).hexdigest()

    settings = Settings(
        postgres_password="test_password",
        gemini_api_key="test-key",
        storage_dir=tmp_path / "storage",
        secrets_dir=tmp_path / "secrets",
    )
    app = create_app(settings, init_db=False)
    mock_session = AsyncMock()

    async def _override_db():
        yield mock_session

    app.dependency_overrides[get_db_session] = _override_db

    cached_doc = Document(
        doc_id="label_cached1",
        filename="label.webp",
        doc_type="label",
        reg_no="農藥進00569",
        volume_tag="1kg",
        content_sha256=sha,
        common_name="Sulfur",
        is_active_baseline=False,
        page_count=1,
        extracted_data=ProductLabelExtraction(
            reg_no=_gf("農藥進00569"),
            common_name=_gf("Sulfur"),
        ).model_dump(),
        uploaded_at=datetime(2026, 11, 22, tzinfo=timezone.utc),
    )

    async def _boom_parse(**kwargs):
        raise AssertionError("Gemini parse should not run on content_sha256 cache hit")

    monkeypatch.setattr("app.api.routes_documents.parse_document_pages", _boom_parse)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            mock_session.execute.return_value = MagicMock(
                scalar_one_or_none=MagicMock(return_value="label_cached1")
            )
            exists_resp = await client.get(
                "/api/documents/exists?filename=label.webp&doc_type=label&reg_no=農藥進00569"
            )
            assert exists_resp.status_code == 200
            assert exists_resp.json() == {"exists": True, "doc_id": "label_cached1"}

            mock_session.execute.return_value = MagicMock(
                scalar_one_or_none=MagicMock(return_value=cached_doc)
            )
            up_resp = await client.post(
                "/api/documents/upload",
                data={"doc_type": "label", "reg_no": "農藥進00569"},
                files={"file": ("label.webp", webp_bytes, "image/webp")},
            )
            assert up_resp.status_code == 201
            assert up_resp.json()["model_used"] == "db-cache"

            # Shared multi-product change_cert included via include_shared_certs=true
            shared_cert = Document(
                doc_id="cert_shared1",
                filename="loa.pdf",
                doc_type="change_cert",
                reg_no=None,
                volume_tag="",
                is_active_baseline=False,
                page_count=2,
                extracted_data=NotarizedCertExtraction(
                    cert_subtype=_gf("loa"),
                    products=[{"common_name": _gf("Sulfur")}],
                ).model_dump(),
                uploaded_at=datetime(2026, 11, 22, tzinfo=timezone.utc),
            )
            mock_session.execute.side_effect = [
                MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[cached_doc])))),
                MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[cached_doc])))),
                MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
                MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[shared_cert])))),
            ]
            list_resp = await client.get(
                "/api/documents?reg_no=農藥進00569&is_active_baseline=true&include_shared_certs=true"
            )
            assert list_resp.status_code == 200
            ids = [d["doc_id"] for d in list_resp.json()]
            assert "label_cached1" in ids
            assert "cert_shared1" in ids
