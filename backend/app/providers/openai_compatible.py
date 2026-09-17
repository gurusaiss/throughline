"""Shared adapter for OpenAI-compatible Chat Completions APIs.

OpenAI, OpenRouter and a long tail of providers speak the same wire format, so the logic lives here once
(rule 59: no duplicated provider logic). Subclasses supply a name, base URL and auth headers.

**Verification note (honesty):** `platform.openai.com` and `openrouter.ai` were blocked by this environment's
egress policy during research, so this adapter targets the long-stable `/v1/chat/completions` contract rather
than a newer endpoint that could not be checked. The operator should confirm against current documentation
before production use; `RESEARCH.md §10` tracks this as an open item.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.core.errors import (
    ContentFiltered,
    ProviderAuthError,
    ProviderInvalidRequest,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.pipeline.types import TokenUsage
from app.providers.base import Capability, ModelRequest, ModelResponse, Provider


class OpenAICompatibleProvider(Provider):
    name = "openai_compatible"
    default_base_url = "https://api.openai.com"
    supports_json_schema = True

    def __init__(self, api_key: str = "", base_url: str = "") -> None:
        super().__init__(api_key=api_key, base_url=base_url or self.default_base_url)
        self._client: httpx.AsyncClient | None = None

    def supports(self, cap: Capability) -> bool:
        return cap in {
            Capability.TEXT,
            Capability.VISION,
            Capability.STRUCTURED_OUTPUT,
            Capability.TOOL_USE,
            Capability.STREAMING,
            Capability.REASONING,
            Capability.LONG_CONTEXT,
        }

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"content-type": "application/json", **self.auth_headers()}
            self._client = httpx.AsyncClient(base_url=self._base_url, headers=headers)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _build_payload(self, req: ModelRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.extend({"role": m.role, "content": m.content} for m in req.messages)

        payload: dict[str, Any] = {
            "model": req.model_id,
            "messages": messages,
            "max_completion_tokens": req.max_output_tokens,
        }
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if req.stop_sequences:
            payload["stop"] = req.stop_sequences

        if req.output_schema is not None and self.supports_json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "result",
                    "strict": True,
                    "schema": _to_strict_schema(req.output_schema),
                },
            }
        elif req.output_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def complete(self, req: ModelRequest) -> ModelResponse:
        client = self._get_client()
        started = time.perf_counter()
        try:
            resp = await client.post(
                "/v1/chat/completions", json=self._build_payload(req), timeout=req.timeout_seconds
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(
                f"{self.name} request timed out after {req.timeout_seconds}s",
                provider=self.name,
                model_id=req.model_id,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"Could not reach {self.name}",
                provider=self.name,
                model_id=req.model_id,
                detail=str(exc),
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if resp.status_code >= 400:
            self._raise_for_status(resp, req)
        return self._parse(resp.json(), req, latency_ms)

    def _raise_for_status(self, resp: httpx.Response, req: ModelRequest) -> None:
        try:
            body = resp.json()
            err = body.get("error", {}) if isinstance(body, dict) else {}
            message = err.get("message", resp.text[:500])
            code = err.get("code", "")
        except ValueError:
            message, code = resp.text[:500], ""

        kw = {"provider": self.name, "model_id": req.model_id, "detail": message}
        if resp.status_code in (401, 403):
            raise ProviderAuthError(f"{self.name} rejected the API key", **kw)
        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after")
            raise ProviderRateLimited(
                f"{self.name} rate limit reached",
                retry_after=float(retry_after) if retry_after else None,
                **kw,
            )
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"{self.name} is unavailable", **kw)
        if code in ("content_filter", "content_policy_violation"):
            raise ContentFiltered(f"{self.name} filtered this content", **kw)
        raise ProviderInvalidRequest(f"{self.name} rejected the request: {message}", **kw)

    def _parse(self, data: dict[str, Any], req: ModelRequest, latency_ms: int) -> ModelResponse:
        choices = data.get("choices") or []
        if not choices:
            raise ProviderUnavailable(
                f"{self.name} returned no choices", provider=self.name, model_id=req.model_id
            )

        choice = choices[0]
        finish_reason = choice.get("finish_reason", "stop")
        if finish_reason == "content_filter":
            raise ContentFiltered(
                f"{self.name} filtered this content", provider=self.name, model_id=req.model_id
            )

        text = (choice.get("message") or {}).get("content") or ""
        parsed: dict[str, Any] | None = None
        if req.output_schema is not None and text:
            try:
                candidate = json.loads(text)
                if isinstance(candidate, dict):
                    parsed = candidate
            except json.JSONDecodeError:
                # Left as None; the deterministic validator reports this as a failed check and the
                # orchestrator may issue a bounded repair attempt (ADR-010).
                parsed = None

        raw_usage = data.get("usage") or {}
        cached = (raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        usage = TokenUsage(
            input_tokens=raw_usage.get("prompt_tokens", 0),
            output_tokens=raw_usage.get("completion_tokens", 0),
            cache_read_tokens=cached,
        )
        return ModelResponse(
            text=text,
            parsed=parsed,
            usage=usage,
            finish_reason=finish_reason,
            model_id=data.get("model", req.model_id),
            provider=self.name,
            latency_ms=latency_ms,
            raw_id=data.get("id", ""),
        )


def _to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strict mode requires `additionalProperties: false` and every property in `required`.

    Rather than reject schemas that do not satisfy this, we normalise them — the caller's schema is still the
    one the local validator enforces, so tightening here cannot weaken the guarantee.
    """

    def walk(node: Any) -> Any:
        if not isinstance(node, dict):
            return [walk(v) for v in node] if isinstance(node, list) else node
        out = {k: walk(v) for k, v in node.items()}
        if out.get("type") == "object" and "properties" in out:
            out.setdefault("additionalProperties", False)
            out["required"] = list(out["properties"].keys())
        return out

    return walk(schema)


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    default_base_url = "https://api.openai.com"


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"
    default_base_url = "https://openrouter.ai/api"
    # OpenRouter proxies many upstreams whose strict-schema support varies, so we use the looser mode and
    # rely on local validation rather than claiming a guarantee we cannot make for every upstream.
    supports_json_schema = False

    def auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": "https://throughline.local",
            "X-Title": "Throughline",
        }
