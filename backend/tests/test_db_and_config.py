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
        "companies",
        "company_users",
        "company_access_requests",
        "moa_registry",
        "documents",
        "document_pages",
        "document_clauses",
        "entity_aliases",
        "audit_reports",
        "aphia_records",
        "aphia_query_cache",
        "chemical_evaluations",
        "company_billing_accounts",
        "usage_ledger",
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


def test_company_tenancy_models_and_alembic_003() -> None:
    from app.db.models import (
        AuditReportCache,
        ChemicalEvaluation,
        Company,
        CompanyAccessRequest,
        CompanyUser,
        EntityAlias,
    )

    dialect = postgresql.dialect()
    for table in (
        Company.__table__,
        CompanyUser.__table__,
        CompanyAccessRequest.__table__,
    ):
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "CREATE TABLE" in ddl

    assert "ck_company_id_safe_path" in str(CreateTable(Company.__table__).compile(dialect=dialect))
    assert "ck_company_user_email_lower" in str(CreateTable(CompanyUser.__table__).compile(dialect=dialect))
    assert "ck_access_request_email_lower" in str(
        CreateTable(CompanyAccessRequest.__table__).compile(dialect=dialect)
    )
    assert any(i.name == "uq_access_requests_pending_email" for i in CompanyAccessRequest.__table__.indexes)

    doc_cols = set(Document.__table__.columns.keys())
    alias_cols = set(EntityAlias.__table__.columns.keys())
    audit_cols = set(AuditReportCache.__table__.columns.keys())
    eval_pk = [c.name for c in ChemicalEvaluation.__table__.primary_key.columns]

    assert "company_id" in doc_cols
    assert "company_id" in alias_cols
    assert "company_id" in audit_cols
    assert eval_pk == ["company_id", "common_name"]

    uq_baseline = next(i for i in Document.__table__.indexes if i.name == "uq_documents_active_baseline")
    assert [c.name for c in uq_baseline.columns] == ["company_id", "reg_no", "doc_type", "volume_tag"]

    uq_alias = next(c for c in EntityAlias.__table__.constraints if c.name == "uq_entity_alias_pair")
    assert [c.name for c in uq_alias.columns] == ["company_id", "zh_name", "en_name"]

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(REPO_ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "backend" / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_current_head() == "004_billing_usage"
