"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import (
    routes_auth,
    routes_catalog,
    routes_engine,
    routes_history,
    routes_prompts,
)
from app.core.config import get_settings
from app.core.errors import ThroughlineError
from app.db.session import dispose_engine, init_engine
from app.pipeline.cache import ExactMatchCache, InMemoryCacheBackend, RedisCacheBackend
from app.pipeline.orchestrator import Orchestrator
from app.providers import ProviderPool
from app.registry.registry import ModelRegistry

logger = logging.getLogger("throughline")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    problems = settings.validate_production()
    if problems:
        # Refuse to boot rather than run a production deployment with a dev secret or wildcard CORS.
        raise RuntimeError("Unsafe production configuration:\n  - " + "\n  - ".join(problems))

    init_engine(settings)
    registry = ModelRegistry.from_catalog()
    pool = ProviderPool(settings)

    backend: object = InMemoryCacheBackend()
    if settings.redis_url and not settings.redis_url.startswith("memory"):
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(settings.redis_url)
            await client.ping()
            backend = RedisCacheBackend(client)
        except Exception as exc:  # pragma: no cover - depends on deployment
            # A cache is an optimisation, never a dependency: degrade to in-process rather than fail boot.
            logger.warning("Redis unavailable (%s); using in-memory cache.", exc)

    app.state.settings = settings
    app.state.registry = registry
    app.state.pool = pool
    app.state.orchestrator = Orchestrator(
        settings=settings,
        registry=registry,
        pool=pool,
        cache=ExactMatchCache(backend, tenant_id="shared", ttl_seconds=settings.cache_ttl_seconds),  # type: ignore[arg-type]
    )

    configured = pool.configured_providers()
    logger.info("Configured providers: %s", sorted(configured) or "none")
    stale = registry.stale_models()
    if stale:
        logger.warning(
            "%d models have unverified or stale pricing: %s",
            len(stale),
            ", ".join(m.id for m in stale[:8]),
        )

    try:
        yield
    finally:
        await pool.aclose()
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Throughline API",
        version="0.1.0",
        description=(
            "Adaptive AI execution layer. Turns intent into an optimised request, spends only as much "
            "orchestration as the request justifies, and evaluates the result before returning it."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    )

    @app.exception_handler(ThroughlineError)
    async def handle_domain_error(_: Request, exc: ThroughlineError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    for module in (routes_auth, routes_engine, routes_catalog, routes_prompts, routes_history):
        app.include_router(module.router, prefix=settings.api_prefix)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "providers_configured": sorted(app.state.pool.configured_providers()),
            "models": len(app.state.registry.models),
            "catalog_version": app.state.registry.catalog_version,
        }

    return app


app = create_app()
