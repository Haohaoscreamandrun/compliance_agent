# cSpell:words asyncpg vertexai jsonb typeof
"""Unit tests for CL 2.1 (Settings, SQLAlchemy URL escaping, Vertex AI toggle, and ORM schema)."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.config import REPO_ROOT, Settings
from app.db.models import Base, Document, DocumentClause, DocumentPage, MoaRegistry
from app.db.session import build_async_engine, build_session_factory, get_db_session


def test_sqlalchemy_url_escapes_special_password_characters() -> None:
    settings = Settings(
        _env_file=None,
        postgres_user="compliance_app",
        postgres_password="p@ss:w/rd#1%2?x",
        postgres_host="db",
        postgres_port=5432,
        postgres_db="compliance_db",
    )
    url = settings.sqlalchemy_database_url
    assert url.drivername == "postgresql+asyncpg"
    assert url.username == "compliance_app"
    assert url.password == "p@ss:w/rd#1%2?x"
    assert url.host == "db"
    assert url.port == 5432
    assert url.database == "compliance_db"
    rendered = url.render_as_string(hide_password=False)
    assert "p%40ss%3Aw%2Frd%231%252%3Fx@db:5432/compliance_db" in rendered


def test_empty_postgres_fields_raise() -> None:
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD must not be empty"):
        _ = Settings(_env_file=None, postgres_password="   ").sqlalchemy_database_url

    with pytest.raises(ValueError, match="POSTGRES_USER, POSTGRES_HOST, and POSTGRES_DB"):
        _ = Settings(
            _env_file=None, postgres_password="secret", postgres_host="  "
        ).sqlalchemy_database_url


def test_genai_credentials_mutual_exclusion_and_adc_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    sa_file = tmp_path / "vertex-sa.json"
    sa_file.write_text('{"type": "service_account"}', encoding="utf-8")

    # Mode 1: AI Studio API Key (default)
    ai_studio = Settings(
        _env_file=None,
        google_genai_use_vertexai=False,
        gemini_api_key="test-key-123",
        google_cloud_project="ignored-project",
    )
    assert ai_studio.validate_genai_credentials() == {"api_key": "test-key-123"}

    # Mode 2: Vertex AI ignores GEMINI_API_KEY and sets GOOGLE_APPLICATION_CREDENTIALS when SA key set
    vertex = Settings(
        _env_file=None,
        google_genai_use_vertexai=True,
        gemini_api_key="should-be-ignored",
        google_cloud_project="my-gcp-proj",
        google_cloud_location="asia-east1",
        secrets_dir=tmp_path,
        gcp_sa_key_filename="vertex-sa.json",
    )
    assert vertex.validate_genai_credentials() == {
        "vertexai": True,
        "project": "my-gcp-proj",
        "location": "asia-east1",
    }
    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(sa_file.resolve())

    # Missing key in AI Studio mode raises
    with pytest.raises(ValueError, match="GEMINI_API_KEY is required"):
        Settings(
            _env_file=None, google_genai_use_vertexai=False, gemini_api_key=""
        ).validate_genai_credentials()

    # Missing project in Vertex AI mode raises
    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT is required"):
        Settings(
            _env_file=None, google_genai_use_vertexai=True, google_cloud_project=""
        ).validate_genai_credentials()


def test_repo_relative_paths_and_secret_traversal_guard() -> None:
    settings = Settings(
        _env_file=None,
        storage_dir=Path("./storage"),
        secrets_dir=Path("./secrets"),
        gcp_sa_key_filename="vertex-sa.json",
    )
    assert settings.storage_dir == (REPO_ROOT / "storage").resolve()
    assert settings.secrets_dir == (REPO_ROOT / "secrets").resolve()
    assert settings.resolved_gcp_sa_key_path == (
        REPO_ROOT / "secrets" / "vertex-sa.json"
    ).resolve()

    with pytest.raises(ValueError, match="must not contain '..'"):
        Settings(_env_file=None, storage_dir=Path("../outside"))

    bad = Settings(
        _env_file=None,
        google_genai_use_vertexai=True,
        google_cloud_project="proj",
        gcp_sa_key_filename="../.env",
    )
    with pytest.raises(ValueError, match="must reside inside SECRETS_DIR"):
        bad.validate_genai_credentials()


def test_cors_origins_env_loading_json_and_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS", '["http://localhost:4200","http://127.0.0.1:4200"]'
    )
    s_json = Settings(_env_file=None)
    assert s_json.cors_allowed_origins == [
        "http://localhost:4200",
        "http://127.0.0.1:4200",
    ]

    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS", "http://localhost:4200, http://localhost:4300"
    )
    s_csv = Settings(_env_file=None)
    assert s_csv.cors_allowed_origins == [
        "http://localhost:4200",
        "http://localhost:4300",
    ]


def test_postgresql_jsonb_ddl_and_cascade_constraints() -> None:
    dialect = postgresql.dialect()
    expected_tables = {
        "moa_registry",
        "documents",
        "document_pages",
        "document_clauses",
    }
    assert set(Base.metadata.tables.keys()) == expected_tables

    for table in (
        MoaRegistry.__table__,
        Document.__table__,
        DocumentPage.__table__,
        DocumentClause.__table__,
    ):
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "CREATE TABLE" in ddl

    doc_ddl = str(CreateTable(Document.__table__).compile(dialect=dialect))
    assert "JSONB" in doc_ddl
    assert "is_active_baseline" in doc_ddl
    assert "ck_doc_type" in doc_ddl

    page_ddl = str(CreateTable(DocumentPage.__table__).compile(dialect=dialect))
    assert "ON DELETE CASCADE" in page_ddl
    assert "uq_doc_page" in page_ddl

    clause_ddl = str(CreateTable(DocumentClause.__table__).compile(dialect=dialect))
    assert "ON DELETE CASCADE" in clause_ddl
    assert "ck_clause_box_2d" in clause_ddl
    assert "jsonb_array_length(box_2d) = 4" in clause_ddl


async def test_session_builder_and_fastapi_dependency() -> None:
    settings = Settings(
        _env_file=None,
        postgres_user="compliance_app",
        postgres_password="strong_password",
        postgres_host="127.0.0.1",
        postgres_port=5432,
        postgres_db="compliance_db",
    )
    engine = build_async_engine(settings)
    factory = build_session_factory(engine)
    fake_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_factory=factory)))

    gen = get_db_session(fake_request)  # type: ignore[arg-type]
    session = await anext(gen)
    assert session is not None
    await gen.aclose()
    await engine.dispose()
