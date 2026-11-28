# cSpell:words asyncpg genai vertexai MOAOPD
"""Application configuration loaded via pydantic-settings v2."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = (
    BACKEND_DIR.parent
    if (BACKEND_DIR.parent / "backend").is_dir()
    else BACKEND_DIR
)
DEFAULT_ENV_FILES = (REPO_ROOT / ".env", Path("../.env"), Path(".env"))


class Settings(BaseSettings):
    """Strongly-typed runtime settings for host CLI and Docker environments."""

    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 1. Google Gemini API / Vertex AI ---
    gemini_api_key: str = Field(default="")
    gemini_parse_model: str = Field(default="gemini-3.8-flash")
    gemini_parse_fallback_models: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
    )
    gemini_parse_lite_model: str = Field(default="gemini-3.5-flash-lite")
    gemini_copilot_model: str = Field(default="gemini-3.8-flash")

    google_genai_use_vertexai: bool = Field(default=False)
    google_cloud_project: str = Field(default="")
    google_cloud_location: str = Field(default="asia-east1")
    gcp_sa_key_filename: str = Field(default="")

    # --- 2. PostgreSQL 16 Database ---
    database_url: str = Field(default="")
    postgres_user: str = Field(default="compliance_app")
    postgres_password: str = Field(default="")
    postgres_db: str = Field(default="compliance_db")
    postgres_host: str = Field(default="127.0.0.1")
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_ssl: bool = Field(default=False)

    # --- 2b. Cloudflare R2 Object Storage (optional; local disk used when r2_bucket is empty) ---
    r2_endpoint_url: str = Field(default="")
    r2_bucket: str = Field(default="")
    r2_access_key_id: str = Field(default="")
    r2_secret_access_key: str = Field(default="")

    # --- 3. Taiwan MOA EIR027 OpenData API ---
    moa_api_base_url: str = Field(
        default="https://data.moa.gov.tw/Service/OpenData/FromM/PesticideData.aspx"
    )
    moa_http_timeout_seconds: float = Field(default=30.0, gt=0)

    # --- 4. Pre-Processing, Storage & Server ---
    storage_dir: Path = Field(default=REPO_ROOT / "storage")
    secrets_dir: Path = Field(default=REPO_ROOT / "secrets")
    max_upload_size_mb: int = Field(default=20, ge=1, le=100)
    page_image_max_edge_px: int = Field(default=2400, ge=512, le=4096)
    page_image_webp_quality: int = Field(default=90, ge=50, le=100)

    backend_host: str = Field(default="127.0.0.1")
    backend_port: int = Field(default=8000, ge=1, le=65535)
    frontend_port: int = Field(default=4200, ge=1, le=65535)
    require_cf_access: bool = Field(default=False)
    public_base_url: str = Field(default="")

    # --- 5. Usage Metering & NewebPay (藍新金流) Billing ---
    billing_free_calls_default: int = Field(default=5, ge=0)
    billing_markup: float = Field(default=1.5, gt=0)
    billing_usd_to_twd: float = Field(default=32.0, gt=0)
    newebpay_merchant_id: str = Field(default="")
    newebpay_hash_key: str = Field(default="")
    newebpay_hash_iv: str = Field(default="")
    newebpay_mpg_url: str = Field(default="https://ccore.newebpay.com/MPG/mpg_gateway")

    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:4200", "http://127.0.0.1:4200"]
    )

    @field_validator("storage_dir", "secrets_dir", mode="before")
    @classmethod
    def _resolve_repo_relative_path(cls, value: Any) -> Path:
        raw_path = Path(value) if not isinstance(value, Path) else value
        if ".." in raw_path.parts:
            raise ValueError("Directory path must not contain '..' traversal segments.")
        if not raw_path.is_absolute():
            return (REPO_ROOT / raw_path).resolve()
        return raw_path.resolve()

    @field_validator("cors_allowed_origins", "gemini_parse_fallback_models", mode="before")
    @classmethod
    def _parse_origins(cls, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(origin).strip() for origin in value if str(origin).strip()]
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    raise ValueError("CORS_ALLOWED_ORIGINS JSON must be a list.")
                return [str(origin).strip() for origin in parsed if str(origin).strip()]
            return [origin.strip() for origin in raw.split(",") if origin.strip()]
        raise ValueError("Invalid CORS_ALLOWED_ORIGINS format")

    @property
    def sqlalchemy_database_url(self) -> URL | str:
        """Build SQLAlchemy URL from DATABASE_URL (stripped for asyncpg) or POSTGRES_* vars."""
        raw_url = self.database_url.strip()
        if raw_url:
            from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

            if raw_url.startswith("postgresql://"):
                raw_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif raw_url.startswith("postgres://"):
                raw_url = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
            parsed = urlparse(raw_url)
            kept = [
                (k, v)
                for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                if k not in ("sslmode", "channel_binding")
            ]
            return urlunparse(parsed._replace(query=urlencode(kept)))

        user = self.postgres_user.strip()
        password = self.postgres_password
        host = self.postgres_host.strip()
        db = self.postgres_db.strip()
        if not password or not password.strip():
            raise ValueError("POSTGRES_PASSWORD must not be empty.")
        if not user or not host or not db:
            raise ValueError("POSTGRES_USER, POSTGRES_HOST, and POSTGRES_DB must not be empty.")
        return URL.create(
            drivername="postgresql+asyncpg",
            username=user,
            password=password,
            host=host,
            port=self.postgres_port,
            database=db,
        )

    @property
    def resolved_gcp_sa_key_path(self) -> Path | None:
        """Return resolved path to GCP service-account key inside SECRETS_DIR when set."""
        filename = self.gcp_sa_key_filename.strip()
        if not filename:
            return None
        raw_rel = Path(filename)
        if raw_rel.is_absolute() or ".." in raw_rel.parts:
            raise ValueError("GCP_SA_KEY_FILENAME must reside inside SECRETS_DIR.")
        base_dir = self.secrets_dir.resolve()
        candidate = (base_dir / raw_rel).resolve()
        if base_dir not in candidate.parents:
            raise ValueError("GCP_SA_KEY_FILENAME must reside inside SECRETS_DIR.")
        return candidate

    def validate_genai_credentials(self) -> dict[str, Any]:
        """Return mutually-exclusive google-genai Client init kwargs per ADR-001."""
        sa_key_path = self.resolved_gcp_sa_key_path
        if self.google_genai_use_vertexai:
            if not self.google_cloud_project.strip():
                raise ValueError(
                    "GOOGLE_CLOUD_PROJECT is required when GOOGLE_GENAI_USE_VERTEXAI=true."
                )
            if sa_key_path is not None:
                if not sa_key_path.is_file():
                    raise ValueError(f"GCP service-account key file not found: {sa_key_path}")
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(sa_key_path)
            return {
                "vertexai": True,
                "project": self.google_cloud_project.strip(),
                "location": self.google_cloud_location.strip() or "asia-east1",
            }
        if not self.gemini_api_key.strip():
            raise ValueError(
                "GEMINI_API_KEY is required when GOOGLE_GENAI_USE_VERTEXAI=false."
            )
        return {"api_key": self.gemini_api_key.strip()}
