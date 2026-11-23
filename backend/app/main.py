# cSpell:words httpx healthz
"""FastAPI application entrypoint for the Taiwan Label Compliance Agent."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_copilot import router as copilot_router
from app.api.routes_documents import router as documents_router
from app.api.routes_entity_aliases import router as entity_aliases_router
from app.api.routes_registry import router as registry_router
from app.config import Settings
from app.db.session import build_async_engine, build_session_factory, init_db_schema
from app.services.genai_client import build_genai_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    init_db: bool = True,
) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    resolved_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved_settings
        engine = None
        try:
            if init_db:
                engine = build_async_engine(resolved_settings)
                app.state.engine = engine
                app.state.session_factory = build_session_factory(engine)
                await init_db_schema(engine)
            else:
                app.state.engine = None
                app.state.session_factory = None

            try:
                app.state.genai_client = build_genai_client(resolved_settings)
            except ValueError as exc:
                log.warning("Gemini client disabled at startup: %s", exc)
                app.state.genai_client = None

            async with httpx.AsyncClient(
                timeout=resolved_settings.moa_http_timeout_seconds
            ) as http_client:
                app.state.http_client = http_client
                yield
        finally:
            if engine is not None:
                await engine.dispose()

    application = FastAPI(
        title="Taiwan Agrochemical Label Compliance Agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["*"],
    )
    application.include_router(registry_router)
    application.include_router(documents_router)
    application.include_router(copilot_router)
    application.include_router(entity_aliases_router)

    @application.get("/health", tags=["health"])
    @application.get("/healthz", include_in_schema=False)
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
