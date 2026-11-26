"""Unit tests for Task 11.3: company_users auth dependency, document/page isolation, and R2 tenant prefixes."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from PIL import Image

from app.config import Settings
from app.db.models import CompanyUser, Document, DocumentPage, PUBLIC_DEMO_COMPANY_ID
from app.db.session import get_db_session
from app.main import create_app
from app.services.preprocessor import ProcessedPage
from app.services.storage import PageStorage


def _make_webp_bytes() -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (20, 20), color=(240, 240, 240)).save(buf, format="WEBP")
    return buf.getvalue()


def test_page_storage_uses_company_prefix_and_rejects_traversal(tmp_path: Path) -> None:
    webp = _make_webp_bytes()
    fake_s3 = MagicMock()
    fake_s3.list_objects_v2.return_value = {
        "Contents": [{"Key": "companies/acme/pages/lbl_1/page_1.webp"}]
    }
    fake_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=webp))}

    settings = Settings(
        _env_file=None,
        storage_dir=tmp_path,
        r2_bucket="test-bucket",
        r2_endpoint_url="https://example.r2.cloudflarestorage.com",
        r2_access_key_id="k",
        r2_secret_access_key="s",
    )
    storage = PageStorage(settings)
    storage._s3_client = lambda: fake_s3  # type: ignore[method-assign]

    page = ProcessedPage(
        page_number=1,
        image_rel_path="pages/lbl_1/page_1.webp",
        webp_bytes=webp,
        width_px=20,
        height_px=20,
    )
    storage.save_pages("lbl_1", [page], company_id="acme")
    assert fake_s3.put_object.call_args.kwargs["Key"] == "companies/acme/pages/lbl_1/page_1.webp"

    assert storage.read_page("lbl_1", "pages/lbl_1/page_1.webp", company_id="acme") == webp
    assert fake_s3.get_object.call_args.kwargs["Key"] == "companies/acme/pages/lbl_1/page_1.webp"

    storage.delete_doc_pages("lbl_1", company_id="acme")
    assert fake_s3.list_objects_v2.call_args.kwargs["Prefix"] == "companies/acme/pages/lbl_1/"

    with pytest.raises(ValueError, match="company_id"):
        storage.save_pages("lbl_1", [page], company_id="../evil")


async def test_documents_require_allowlisted_company_user_and_isolate_pages(tmp_path: Path) -> None:
    app = create_app(
        Settings(_env_file=None, storage_dir=tmp_path, require_cf_access=True),
        init_db=False,
    )
    session = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Missing X-Auth-User-Email when require_cf_access=True -> 401
        resp_unauth = await client.get("/api/documents")
        assert resp_unauth.status_code == 401

        # 2. Email not in company_users allowlist -> 403
        session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        resp_forbidden = await client.get(
            "/api/documents",
            headers={"x-auth-user-email": "stranger@gmail.com"},
        )
        assert resp_forbidden.status_code == 403

        # 3. Company B cannot read Company A's page image -> 404
        user_b = CompanyUser(email="bob@compb.com", company_id="comp_b", role="member")
        doc_a = Document(
            doc_id="doc_a",
            company_id="comp_a",
            filename="a.pdf",
            doc_type="label",
            page_count=1,
            extracted_data={},
            uploaded_at=datetime.now(timezone.utc),
        )
        page_a = DocumentPage(
            doc_id="doc_a",
            page_number=1,
            image_rel_path="pages/doc_a/page_1.webp",
            width_px=20,
            height_px=20,
        )
        session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=user_b)),
            MagicMock(first=MagicMock(return_value=(doc_a, page_a))),
        ]
        resp_cross = await client.get(
            "/api/documents/doc_a/pages/1/image",
            headers={"x-auth-user-email": "bob@compb.com"},
        )
        assert resp_cross.status_code == 404

        # 4. Unauthenticated user CAN read public_demo page image (with public cache header)
        demo_dir = tmp_path / "pages" / "seed_demo"
        demo_dir.mkdir(parents=True, exist_ok=True)
        (demo_dir / "page_1.webp").write_bytes(_make_webp_bytes())
        demo_doc = Document(
            doc_id="seed_demo",
            company_id=PUBLIC_DEMO_COMPANY_ID,
            filename="demo.webp",
            doc_type="label",
            page_count=1,
            extracted_data={},
            uploaded_at=datetime.now(timezone.utc),
        )
        demo_page = DocumentPage(
            doc_id="seed_demo",
            page_number=1,
            image_rel_path="pages/seed_demo/page_1.webp",
            width_px=20,
            height_px=20,
        )
        session.execute.side_effect = [
            MagicMock(first=MagicMock(return_value=(demo_doc, demo_page))),
        ]
        resp_demo = await client.get("/api/documents/seed_demo/pages/1/image")
        assert resp_demo.status_code == 200
        assert "public" in resp_demo.headers.get("cache-control", "")


async def test_public_demo_workspace_switcher_authorization_and_guardrails(tmp_path: Path) -> None:
    app = create_app(
        Settings(_env_file=None, storage_dir=tmp_path, require_cf_access=True, gemini_api_key="test-key"),
        init_db=False,
    )
    session = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: session

    default_admin = CompanyUser(email="owner@example.com", company_id="default", role="admin")
    other_company_admin = CompanyUser(email="boss@compb.com", company_id="comp_b", role="admin")

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Other company's admin cannot switch into public_demo -> 403
            session.execute.return_value = MagicMock(
                scalar_one_or_none=MagicMock(return_value=other_company_admin)
            )
            resp_other = await client.get(
                "/api/me",
                headers={
                    "x-auth-user-email": "boss@compb.com",
                    "x-workspace-company-id": "public_demo",
                },
            )
            assert resp_other.status_code == 403

            # 2. Default admin cannot switch into arbitrary tenant -> 400
            session.execute.return_value = MagicMock(
                scalar_one_or_none=MagicMock(return_value=default_admin)
            )
            resp_invalid = await client.get(
                "/api/me",
                headers={
                    "x-auth-user-email": "owner@example.com",
                    "x-workspace-company-id": "comp_b",
                },
            )
            assert resp_invalid.status_code == 400

            # 3. Default admin CAN switch into public_demo and sees can_manage_public_demo=True
            session.execute.side_effect = [
                MagicMock(scalar_one_or_none=MagicMock(return_value=default_admin)),
                MagicMock(scalar_one_or_none=MagicMock(return_value="Public Demo")),
            ]
            resp_me = await client.get(
                "/api/me",
                headers={
                    "x-auth-user-email": "owner@example.com",
                    "x-workspace-company-id": "public_demo",
                },
            )
            assert resp_me.status_code == 200
            body = resp_me.json()
            assert body["company_id"] == "public_demo"
            assert body["can_manage_public_demo"] is True

            # 4. In public_demo mode, uploading or auditing a real permit (non-99001) is blocked with 400
            session.execute.side_effect = None
            session.execute.return_value = MagicMock(
                scalar_one_or_none=MagicMock(return_value=default_admin)
            )
            resp_real_upload = await client.post(
                "/api/documents/upload",
                headers={
                    "x-auth-user-email": "owner@example.com",
                    "x-workspace-company-id": "public_demo",
                },
                data={"doc_type": "label", "reg_no": "農藥進00569"},
                files={"file": ("real.webp", _make_webp_bytes(), "image/webp")},
            )
            assert resp_real_upload.status_code == 400

            resp_real_audit = await client.post(
                "/api/copilot/audit/農藥進00569",
                headers={
                    "x-auth-user-email": "owner@example.com",
                    "x-workspace-company-id": "public_demo",
                },
            )
            assert resp_real_audit.status_code == 400
