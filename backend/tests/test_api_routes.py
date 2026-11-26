# cSpell:words httpx webp APHIA healthz
"""FastAPI route tests for CL 4.3 (registry, documents, copilot, health, and seed)."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from google.genai import errors as genai_errors
from PIL import Image

from app.config import Settings
from app.db.models import Document, DocumentClause, DocumentPage, MoaRegistry
from app.db.seed import seed_sample_data
from app.db.session import get_db_session
from app.main import create_app
from app.schemas.extraction import ProductLabelExtraction
from app.schemas.grounded import GroundedField
from app.schemas.report import ComplianceAuditReport
from app.services.moa_client import MoaNotFoundError, MoaUnavailableError
from app.services.parser import GeminiParseError, ParseResult


def _make_webp_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 64), (255, 255, 255)).save(buf, format="WEBP")
    return buf.getvalue()


def _gf(verbatim: str, value: str | None = None) -> GroundedField[str]:
    return GroundedField[str](
        verbatim_text=verbatim,
        value=value if value is not None else verbatim,
        page=1,
        box_2d=[100, 100, 200, 400],
    )


def _test_settings(tmp_path: Path, *, gemini_key: str = "test-key-not-real") -> Settings:
    return Settings(
        gemini_api_key=gemini_key,
        postgres_password="test-password",
        storage_dir=tmp_path,
        max_upload_size_mb=1,
    )


@pytest.mark.asyncio
async def test_health_and_registry_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(_test_settings(tmp_path), init_db=False)
    mock_session = AsyncMock()

    async def _override_db():
        yield mock_session

    app.dependency_overrides[get_db_session] = _override_db

    moa_row = MoaRegistry(
        reg_no="農藥進03225",
        permit_type="農藥進",
        permit_number="03225",
        chinese_name="美氟綜",
        english_name="METAFLUMIZONE",
        company_name="台灣巴斯夫股份有限公司",
        raw_core_json={},
        fetched_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    async def _ok_snap(session, http, base_url, reg_no):
        return SimpleNamespace(snapshot=moa_row, stale=False)

    monkeypatch.setattr("app.api.routes_registry.get_registry_snapshot", _ok_snap)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/healthz")).status_code == 200

            r = await client.get("/api/registry/農藥進字03225")
            assert r.status_code == 200
            assert r.json()["reg_no"] == "農藥進03225"
            assert r.json()["stale"] is False

            # Strict validate_reg_no rejects arbitrary digit-suffixed strings
            assert (await client.get("/api/registry/invalid123")).status_code == 400
            assert (await client.get("/api/registry/bad農藥進03225junk")).status_code == 400

            async def _not_found(s, h, b, reg):
                raise MoaNotFoundError("not found")

            monkeypatch.setattr("app.api.routes_registry.get_registry_snapshot", _not_found)
            assert (await client.get("/api/registry/農藥進99999")).status_code == 404

            # Seeded synthetic PUBLIC_DEMO_REG_NO serves cached MoaRegistry without calling live MOA
            demo_moa = MoaRegistry(
                reg_no="農藥進99001",
                permit_type="農藥進",
                permit_number="99001",
                chinese_name="示範殺菌劑",
                english_name="DEMOFUNGIN",
                company_name="示範農化股份有限公司",
                raw_core_json={"synthetic_demo": True},
                fetched_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            )
            mock_session.get.return_value = demo_moa
            demo_resp = await client.get("/api/registry/農藥進99001")
            assert demo_resp.status_code == 200
            assert demo_resp.json()["reg_no"] == "農藥進99001"
            assert demo_resp.json()["company_name"] == "示範農化股份有限公司"
            assert demo_resp.json()["stale"] is False
            mock_session.get.return_value = None

            async def _unavail(s, h, b, reg):
                raise MoaUnavailableError("down")

            monkeypatch.setattr("app.api.routes_registry.get_registry_snapshot", _unavail)
            assert (await client.get("/api/registry/農藥進03225")).status_code == 503


@pytest.mark.asyncio
async def test_document_upload_listing_cleanup_and_page_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(_test_settings(tmp_path), init_db=False)
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    async def _override_db():
        yield mock_session

    app.dependency_overrides[get_db_session] = _override_db

    # Signature matches real parse_document_pages(client, model_name, doc_type, pages, fallback_models=None)
    async def _fake_parse(client, model_name, doc_type, pages, fallback_models=None):
        return ParseResult(
            extraction=ProductLabelExtraction(
                reg_no=_gf("農藥進03225"),
                common_name=_gf("美氟綜"),
            ),
            model_used="gemini-3.8-flash",
            used_fallback=False,
        )

    monkeypatch.setattr("app.api.routes_documents.parse_document_pages", _fake_parse)

    async with app.router.lifespan_context(app):
        def _fake_refresh(obj):
            if getattr(obj, "uploaded_at", None) is None:
                obj.uploaded_at = datetime(2026, 3, 1, tzinfo=timezone.utc)

        mock_session.refresh.side_effect = _fake_refresh

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            webp_bytes = _make_webp_bytes()
            # First upload: no identical prior record -> is_active_baseline is False
            mock_session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
            up = await client.post(
                "/api/documents/upload",
                data={"doc_type": "label", "reg_no": "農藥進03225"},
                files={"file": (r"C:\fakepath\label.webp", webp_bytes, "image/webp")},
            )
            assert up.status_code == 201
            body = up.json()
            doc_id = body["doc_id"]
            assert body["filename"] == "label.webp"
            assert body["reg_no"] == "農藥進03225"
            assert body["is_active_baseline"] is False

            # Re-upload with identical (filename, doc_type, reg_no, common_name, page_count) -> auto baseline True
            mock_session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=doc_id))
            reup = await client.post(
                "/api/documents/upload",
                data={"doc_type": "label", "reg_no": "農藥進03225"},
                files={"file": ("label.webp", webp_bytes, "image/webp")},
            )
            assert reup.status_code == 201
            assert reup.json()["is_active_baseline"] is True
            reup_doc_id = reup.json()["doc_id"]

            # Verify Document, DocumentPage, and DocumentClause rows were added
            added_types = [type(call.args[0]) for call in mock_session.add.call_args_list]
            assert Document in added_types
            assert DocumentPage in added_types
            assert DocumentClause in added_types

            # Conflict between explicit reg_no and extracted reg_no -> 400 and cleans up disk pages
            conflict = await client.post(
                "/api/documents/upload",
                data={"doc_type": "label", "reg_no": "農藥進99999"},
                files={"file": ("label.webp", webp_bytes, "image/webp")},
            )
            assert conflict.status_code == 400
            assert len(list((tmp_path / "pages").iterdir())) == 2  # doc_id + reup_doc_id remain

            # Gemini failure cleans up disk pages and returns 502
            async def _fail_parse(client, model_name, doc_type, pages, fallback_models=None):
                raise GeminiParseError("blocked")

            monkeypatch.setattr("app.api.routes_documents.parse_document_pages", _fail_parse)
            fail_resp = await client.post(
                "/api/documents/upload",
                data={"doc_type": "label", "reg_no": "農藥進03225"},
                files={"file": ("label.webp", webp_bytes, "image/webp")},
            )
            assert fail_resp.status_code == 502
            assert len(list((tmp_path / "pages").iterdir())) == 2

            # Bad doc_type / oversized -> 422 / 413
            assert (
                await client.post(
                    "/api/documents/upload",
                    data={"doc_type": "invalid"},
                    files={"file": ("label.webp", webp_bytes, "image/webp")},
                )
            ).status_code == 422
            assert (
                await client.post(
                    "/api/documents/upload",
                    data={"doc_type": "label"},
                    files={"file": ("big.webp", b"x" * (1024 * 1024 + 5), "image/webp")},
                )
            ).status_code == 413

            # GET /api/documents filters & whitespace doc_type -> 400
            doc_row = Document(
                doc_id=doc_id,
                company_id="default",
                filename="label.webp",
                doc_type="label",
                reg_no="農藥進03225",
                common_name="美氟綜",
                is_active_baseline=False,
                page_count=1,
                extracted_data={},
                uploaded_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            )
            mock_session.execute.return_value = MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[doc_row])))
            )
            listed = await client.get(
                "/api/documents?reg_no=農藥進字03225&doc_type=label&common_name=美氟綜&product_internal_code=BAS%20175&limit=10&offset=0"
            )
            assert listed.status_code == 200 and len(listed.json()) == 1
            compiled_sql = str(mock_session.execute.call_args.args[0])
            assert "document_clauses" in compiled_sql
            assert (await client.get("/api/documents?doc_type=%20")).status_code == 422

            # Serve page image + Cache-Control header + 400/404 guards
            page_row = DocumentPage(
                doc_id=doc_id,
                page_number=1,
                image_rel_path=f"pages/{doc_id}/page_1.webp",
                width_px=64,
                height_px=64,
            )
            mock_session.execute.return_value = MagicMock(first=MagicMock(return_value=(doc_row, page_row)))
            img_resp = await client.get(f"/api/documents/{doc_id}/pages/1/image")
            assert img_resp.status_code == 200
            assert img_resp.headers["content-type"] == "image/webp"
            assert "private" in img_resp.headers["cache-control"]

            assert (await client.get(f"/api/documents/{doc_id}/pages/0/image")).status_code == 400
            assert (await client.get("/api/documents/CON/pages/1/image")).status_code == 400

            # Toggle baseline (rejects reg_no=None with 400, accepts valid reg_no)
            no_reg_doc = Document(
                doc_id="doc_noreg",
                filename="let.pdf",
                doc_type="official_letter",
                reg_no=None,
                is_active_baseline=False,
                page_count=1,
                extracted_data={},
                uploaded_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            )
            mock_session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=no_reg_doc))
            assert (
                await client.patch("/api/documents/doc_noreg/baseline", json={"is_active_baseline": True})
            ).status_code == 400

            mock_session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=doc_row))
            patch_resp = await client.patch(
                f"/api/documents/{doc_id}/baseline",
                json={"is_active_baseline": True},
            )
            assert patch_resp.status_code == 200
            assert patch_resp.json()["is_active_baseline"] is True

            # 'other' cert without reg_no -> 400; with reg_no + manual_notes -> 201
            from app.schemas.extraction import NotarizedCertExtraction

            async def _other_parse(client, model_name, doc_type, pages, fallback_models=None):
                return ParseResult(
                    extraction=NotarizedCertExtraction(cert_subtype=_gf("Other", "other")),
                    model_used="gemini-3.8-flash",
                    used_fallback=False,
                )

            monkeypatch.setattr("app.api.routes_documents.parse_document_pages", _other_parse)
            assert (
                await client.post(
                    "/api/documents/upload",
                    data={"doc_type": "change_cert"},
                    files={"file": ("other.webp", webp_bytes, "image/webp")},
                )
            ).status_code == 400

            mock_session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
            ok_other = await client.post(
                "/api/documents/upload",
                data={"doc_type": "change_cert", "reg_no": "農藥進03225", "manual_notes": "Factory name addendum"},
                files={"file": ("other.webp", webp_bytes, "image/webp")},
            )
            assert ok_other.status_code == 201
            assert ok_other.json()["extracted_data"]["manual_notes"] == "Factory name addendum"


@pytest.mark.asyncio
async def test_copilot_audit_route_errors_and_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Missing Gemini client -> 503
    app_no_key = create_app(_test_settings(tmp_path, gemini_key=""), init_db=False)
    mock_session = AsyncMock()

    async def _override_db():
        yield mock_session

    app_no_key.dependency_overrides[get_db_session] = _override_db
    async with app_no_key.router.lifespan_context(app_no_key):
        transport = httpx.ASGITransport(app=app_no_key)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/api/copilot/audit/農藥進03225")).status_code == 503

    app = create_app(_test_settings(tmp_path), init_db=False)
    app.dependency_overrides[get_db_session] = _override_db

    sample_report = ComplianceAuditReport(
        reg_no="農藥進03225",
        overall_status="needs_verification",
        executive_summary_zh="尚未上傳現行標籤，需人工確認。",
        executive_summary_en="No baseline label uploaded; verification required.",
    )

    async def _fake_audit(**kwargs):
        return sample_report

    monkeypatch.setattr("app.api.routes_copilot.run_compliance_audit", _fake_audit)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ok = await client.post("/api/copilot/audit/農藥進字03225")
            assert ok.status_code == 200
            assert ok.json()["overall_status"] == "needs_verification"

            assert (await client.post("/api/copilot/audit/invalid123")).status_code == 400

            async def _api_err(**kwargs):
                raise genai_errors.APIError(503, {"error": {"message": "overloaded"}}, None)

            monkeypatch.setattr("app.api.routes_copilot.run_compliance_audit", _api_err)
            assert (await client.post("/api/copilot/audit/農藥進03225")).status_code == 502

    # Verify seed_sample_data inserts public_demo company + MOA + 4 Documents + DocumentClauses idempotently
    seed_session = AsyncMock()
    seed_session.add = MagicMock()
    seed_session.execute.side_effect = [
        MagicMock(),  # public_demo Company insert
        MagicMock(),  # MOA insert
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # existing baseline check
        MagicMock(scalar_one_or_none=MagicMock(return_value="seed_label_99001")),
        MagicMock(scalar_one_or_none=MagicMock(return_value="seed_msds_99001_v4")),
        MagicMock(scalar_one_or_none=MagicMock(return_value="seed_cert_99001")),
        MagicMock(scalar_one_or_none=MagicMock(return_value="seed_letter_99001")),
        MagicMock(),  # AuditReportCache demo insert
    ]
    await seed_sample_data(seed_session)
    assert seed_session.add.call_count > 0
    assert all(isinstance(c.args[0], DocumentClause) for c in seed_session.add.call_args_list)
    seed_session.commit.assert_awaited_once()
