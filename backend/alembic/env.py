"""Alembic async migration environment wired to Settings().sqlalchemy_database_url."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import Settings
from app.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url_and_ssl() -> tuple[str, bool]:
    env_url = os.getenv("DATABASE_URL", "").strip()
    if env_url:
        if env_url.startswith("postgresql://"):
            env_url = env_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        parsed = urlparse(env_url)
        query_items = parse_qsl(parsed.query, keep_blank_values=True)
        use_ssl = any(k == "sslmode" and v.lower() in ("require", "verify-ca", "verify-full") for k, v in query_items)
        filtered = [(k, v) for k, v in query_items if k not in ("sslmode", "channel_binding")]
        cleaned_url = urlunparse(parsed._replace(query=urlencode(filtered)))
        return cleaned_url, use_ssl or Settings().postgres_ssl
    settings = Settings()
    raw = settings.sqlalchemy_database_url
    url_str = raw if isinstance(raw, str) else raw.render_as_string(hide_password=False)
    use_ssl = settings.postgres_ssl or "sslmode=require" in settings.database_url
    return url_str, use_ssl


def run_migrations_offline() -> None:
    url, _ = _resolve_url_and_ssl()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    url, use_ssl = _resolve_url_and_ssl()
    cfg["sqlalchemy.url"] = url
    connect_args = {"ssl": "require"} if use_ssl else {}
    connectable = async_engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
