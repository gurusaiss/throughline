"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import (
    routes_account,
    routes_admin,
    routes_apikeys,
    routes_auth,
    routes_catalog,
    routes_engine,
    routes_history,
    routes_prompts,
)
from app.core.config import Settings, get_settings
from app.core.errors import ThroughlineError
from app.core.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
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
    redis_client = None
    if settings.redis_url and not settings.redis_url.startswith("memory"):
        try:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(settings.redis_url)
            await redis_client.ping()
            backend = RedisCacheBackend(redis_client)
        except Exception as exc:  # pragma: no cover - depends on deployment
            # A cache is an optimisation, never a dependency: degrade to in-process rather than fail boot.
            logger.warning("Redis unavailable (%s); using in-memory cache and an in-process rate limiter.", exc)
            redis_client = None

    app.state.settings = settings
    app.state.registry = registry
    app.state.pool = pool
    app.state.redis_client = redis_client
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


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app.

    `settings` is injectable (rather than always reading the process-wide `get_settings()` cache)
    specifically so tests can configure CORS/rate-limiting/security-header behaviour per test — without
    this, a test building its own `Settings(...)` for dependency injection would silently have no effect
    on these middlewares, which closed over whatever `get_settings()` happened to return (and
    `lru_cache`s it for the rest of the process). Production code still calls `create_app()` with no
    argument and gets the real cached settings, unchanged.
    """
    settings = settings or get_settings()
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

    # Starlette wraps middleware so that the MOST RECENTLY added call ends up OUTERMOST (it sees a
    # request first and a response last). RateLimit and SecurityHeaders are therefore registered BEFORE
    # CORS, so CORS stays outermost — the standard, recommended position — meaning every response,
    # including an early 429 from the rate limiter or an error from ExceptionMiddleware, still gets
    # correct CORS headers rather than showing the browser an opaque CORS failure instead of the real
    # status code.
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
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

    for module in (
        routes_auth,
        routes_account,
        routes_apikeys,
        routes_admin,
        routes_engine,
        routes_catalog,
        routes_prompts,
        routes_history,
    ):
        app.include_router(module.router, prefix=settings.api_prefix)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, object]:
        """Liveness: is the process up at all. Never checks dependencies — see /health/ready for that."""
        return {
            "status": "ok",
            "providers_configured": sorted(app.state.pool.configured_providers()),
            "models": len(app.state.registry.models),
            "catalog_version": app.state.registry.catalog_version,
        }

    @app.get("/health/ready", tags=["meta"])
    async def health_ready() -> JSONResponse:
        """Readiness: can this instance actually serve traffic right now.

        A production orchestrator (Kubernetes, a load balancer health check) should route traffic based
        on THIS endpoint, not /health — a process can be alive while its database connection is down.
        """
        from sqlalchemy import text

        from app.db.session import get_sessionmaker

        checks: dict[str, str] = {}
        healthy = True

        try:
            async with get_sessionmaker()() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"unavailable: {exc}"
            healthy = False

        redis_client = getattr(app.state, "redis_client", None)
        if redis_client is None:
            # Not a failure: Redis is an optimisation (cache + distributed rate limiting), and the app is
            # designed to degrade gracefully without it (ADR-005, core/middleware.py), never to depend on it.
            checks["redis"] = "not configured (degraded mode: in-process cache and rate limiting)"
        else:
            try:
                await redis_client.ping()
                checks["redis"] = "ok"
            except Exception as exc:
                checks["redis"] = f"unavailable: {exc}"
                # Redis being down is a degradation, not an outage — the app still serves correctly
                # without it, so it does not flip `healthy` to False the way a database outage does.

        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ready" if healthy else "not_ready", "checks": checks},
        )

    return app


app = create_app()
