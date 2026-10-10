"""Security headers and rate limiting.

Two concerns that belong together operationally (both sit in front of every request) but are logically
independent, so they are implemented as two separate ASGI middlewares rather than one do-everything class.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings


# --------------------------------------------------------------------------------------
# Security headers
# --------------------------------------------------------------------------------------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defence-in-depth headers. None of these replace authentication/authorization — they reduce the
    blast radius of classes of bugs this API should not have, but a sibling service embedding this one's
    responses should not assume these headers alone make that safe.
    """

    def __init__(self, app: Any, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # The legacy X-XSS-Protection auditor is widely considered actively harmful (it has its own
        # exploitable parsing bugs in older browsers) — modern guidance is to explicitly disable it
        # rather than omit the header, which some scanners flag as "not considered."
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        # HSTS over plain HTTP in development would tell the developer's browser to force HTTPS on
        # localhost for the max-age duration, which breaks local HTTP dev servers long after this
        # process exits. Only ever sent when we know we're actually running in production.
        if self._settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"

        # This is a JSON API; it serves no HTML of its own except the Swagger UI at /docs, which loads
        # its JS/CSS from a CDN and needs a looser policy or it breaks. Everywhere else gets the
        # strict policy — a JSON API has no business executing any script or loading any frame.
        if request.url.path.startswith("/docs") or request.url.path.startswith("/redoc"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com"
            )
        else:
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"

        return response


# --------------------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------------------
class _InProcessWindow:
    """Fixed-window counter, per process. Used when Redis is unavailable.

    Deliberately NOT presented as a real distributed limit: with multiple worker processes each has its
    own counter, so the effective ceiling is (limit * worker_count), and it resets on every restart. This
    is an honest degrade-gracefully fallback, not a silent substitute for the real thing — see the
    docstring on RateLimitMiddleware.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))

    def hit(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        now = time.time()
        count, window_start = self._buckets[key]
        if now - window_start >= window_seconds:
            count, window_start = 0, now
        count += 1
        self._buckets[key] = (count, window_start)
        retry_after = max(1, int(window_seconds - (now - window_start)))
        return count <= limit, retry_after


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding/fixed window limiter keyed by (client identity, endpoint class).

    Redis-backed when a Redis client is reachable (shared across workers, survives restarts — a real
    distributed limit); falls back to an in-process approximation otherwise (see `_InProcessWindow`).
    A rate limiter is a defence, not a feature: it must never be a hard dependency that takes the whole
    API down if Redis has a bad moment, so a Redis error during the check fails OPEN (allows the request)
    rather than failing closed.
    """

    AUTH_PATHS = ("/auth/login", "/auth/signup")
    EXPENSIVE_PATHS = ("/execute", "/compare")
    EXEMPT_PATHS = ("/health",)

    def __init__(self, app: Any, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings
        # No redis_client parameter: Starlette constructs this middleware once, when add_middleware() is
        # called at app-creation time — BEFORE `lifespan` has run and created the real Redis client. A
        # client captured here at __init__ would permanently be None. Instead, the real client (set on
        # app.state by lifespan once it connects) is looked up fresh on every request in `_check`.
        self._fallback = _InProcessWindow()

    def _classify(self, path: str) -> tuple[str, int]:
        if any(path.endswith(p) for p in self.AUTH_PATHS):
            return "auth", self._settings.rate_limit_auth_per_minute
        if any(path.endswith(p) for p in self.EXPENSIVE_PATHS):
            return "expensive", self._settings.rate_limit_expensive_per_minute
        return "general", self._settings.rate_limit_general_per_minute

    def _client_ip(self, request: Request) -> str:
        # Blindly trusting X-Forwarded-For lets ANY caller forge their own rate-limit identity simply by
        # sending that header themselves — it is only safe to read when a trusted reverse proxy is known
        # to set it and strip any client-supplied value first. That trust is an explicit operator
        # decision (TRUST_PROXY_HEADERS), never assumed.
        if self._settings.trust_proxy_headers:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if not self._settings.enable_rate_limiting or any(path.endswith(p) for p in self.EXEMPT_PATHS):
            return await call_next(request)

        bucket, limit = self._classify(path)
        key = f"ratelimit:{bucket}:{self._client_ip(request)}"

        redis_client = getattr(request.app.state, "redis_client", None)
        allowed, retry_after = await self._check(key, limit, redis_client)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "code": "rate_limited",
                    "message": "Too many requests. Please slow down and try again shortly.",
                },
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)

    async def _check(self, key: str, limit: int, redis_client: Any | None) -> tuple[bool, int]:
        if redis_client is not None:
            try:
                count = await redis_client.incr(key)
                if count == 1:
                    await redis_client.expire(key, 60)
                ttl = await redis_client.ttl(key)
                return count <= limit, max(1, ttl)
            except Exception:
                # Fail open: a Redis hiccup must degrade the rate limiter, never the API itself.
                pass
        return self._fallback.hit(key, limit)
