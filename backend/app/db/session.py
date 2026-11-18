# cSpell:words asyncpg sessionmaker
"""SQLAlchemy 2.0 AsyncEngine and FastAPI session dependency."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
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
    return create_async_engine(
        settings.sqlalchemy_database_url,
        pool_pre_ping=True,
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


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a scoped AsyncSession from app.state.session_factory."""
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        yield session
