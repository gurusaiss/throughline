"""Exact-match response cache (ADR-005).

Deliberately *not* a semantic cache. RESEARCH 7.1 is decisive: a false semantic hit returns a confidently
wrong answer with a 200 OK, and is invisible without dedicated instrumentation. Exact matching has zero
correctness risk while still capturing the real savings — regenerations, A/B comparisons, repeated
benchmark runs and retries all hit the same key.

The key includes the tenant, so one org can never be served another org's content (ARCHITECTURE §7).
"""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from app.providers.base import ModelRequest


class CacheBackend(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


class InMemoryCacheBackend:
    """Process-local backend for tests and single-process development."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:  # noqa: ARG002
        self._data[key] = value

    def clear(self) -> None:
        self._data.clear()


class RedisCacheBackend:
    def __init__(self, client: object) -> None:
        self._client = client

    async def get(self, key: str) -> str | None:
        value = await self._client.get(key)  # type: ignore[attr-defined]
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._client.set(key, value, ex=ttl_seconds)  # type: ignore[attr-defined]


class ExactMatchCache:
    def __init__(self, backend: CacheBackend, *, tenant_id: str, ttl_seconds: int = 3600) -> None:
        self._backend = backend
        self._tenant_id = tenant_id
        self._ttl = ttl_seconds

    def key(self, req: ModelRequest) -> str:
        """Hash every input that can change the output.

        Anything omitted here is a potential wrong answer served from cache, so the set is deliberately
        complete: model, system, messages, schema, temperature, output limit and stop sequences.
        """
        payload = json.dumps(
            {
                "tenant": self._tenant_id,
                "model": req.model_id,
                "system": req.system,
                "messages": [(m.role, m.content) for m in req.messages],
                "schema": req.output_schema,
                "temperature": req.temperature,
                "max_output_tokens": req.max_output_tokens,
                "stop": req.stop_sequences,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()
        return f"tl:resp:{self._tenant_id}:{digest}"

    async def get(self, key: str) -> str | None:
        try:
            return await self._backend.get(key)
        except Exception:
            # A cache outage must degrade to a live call, never fail the request.
            return None

    async def set(self, key: str, value: str) -> None:
        if not value.strip():
            return  # never cache an empty response
        try:
            await self._backend.set(key, value, self._ttl)
        except Exception:
            return
