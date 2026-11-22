# cSpell:words asyncpg sessionmaker
"""SQLAlchemy 2.0 AsyncEngine and FastAPI session dependency."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.db.models import Base


def build_async_engine(settings: Settings) -> AsyncEngine:
    """Create an async SQLAlchemy engine using the safe URL.create() builder."""
    connect_args = {"ssl": "require"} if settings.postgres_ssl else {}
    return create_async_engine(
        settings.sqlalchemy_database_url,
        pool_pre_ping=True,
        connect_args=connect_args,
        future=True,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async sessionmaker with expire_on_commit=False."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def init_db_schema(engine: AsyncEngine) -> None:
    """Create all PostgreSQL tables defined in Base.metadata if they do not exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name == "postgresql":
            await conn.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS "
                    "volume_tag VARCHAR(64) NOT NULL DEFAULT ''"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS "
                    "content_sha256 VARCHAR(64)"
                )
            )
            await conn.execute(text("DROP INDEX IF EXISTS uq_documents_active_baseline"))
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_active_baseline "
                    "ON documents (reg_no, doc_type, volume_tag) "
                    "WHERE is_active_baseline IS TRUE AND reg_no IS NOT NULL"
                )
            )


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a scoped AsyncSession from app.state.session_factory."""
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        yield session
