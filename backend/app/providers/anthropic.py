"""Anthropic Messages API adapter.

Pricing and caching semantics for this provider were verified against primary documentation during research
(RESEARCH 3.1) — it is the one provider whose catalogue entries are `verified: true`.

Structured output is implemented via forced tool use, which is Anthropic's documented mechanism and is stable
across model generations. Per ADR-010 the core re-validates the result against the full schema regardless.
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

ANTHROPIC_VERSION = "2023-06-01"
STRUCTURED_TOOL_NAME = "emit_result"


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str = "", base_url: str = "https://api.anthropic.com") -> None:
        super().__init__(api_key=api_key, base_url=base_url or "https://api.anthropic.com")
        self._client: httpx.AsyncClient | None = None

    def supports(self, cap: Capability) -> bool:
        return cap in {
            Capability.TEXT,
            Capability.VISION,
            Capability.STRUCTURED_OUTPUT,
            Capability.TOOL_USE,
            Capability.STREAMING,
            Capability.REASONING,
            Capability.PROMPT_CACHING,
            Capability.LONG_CONTEXT,
        }

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _build_payload(self, req: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": req.model_id,
            "max_tokens": req.max_output_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
        }
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if req.stop_sequences:
            payload["stop_sequences"] = req.stop_sequences

        if req.system:
            # System is sent as a content block list so a cache breakpoint can be attached. The compiler
            # orders the prompt stable-prefix-first precisely so this cache covers as much as possible
            # (RESEARCH 7.2: a cache read costs 0.1x and a 5-minute write 1.25x, so it pays back after one).
            block: dict[str, Any] = {"type": "text", "text": req.system}
            if req.enable_prompt_cache and len(req.system) > 2000:
                block["cache_control"] = {"type": "ephemeral"}
            payload["system"] = [block]

        if req.output_schema is not None:
            payload["tools"] = [
                {
                    "name": STRUCTURED_TOOL_NAME,
                    "description": "Return the result using this exact schema.",
                    "input_schema": _to_anthropic_schema(req.output_schema),
                }
            ]
            payload["tool_choice"] = {"type": "tool", "name": STRUCTURED_TOOL_NAME}
        return payload

    async def complete(self, req: ModelRequest) -> ModelResponse:
        client = self._get_client()
        started = time.perf_counter()
        try:
            resp = await client.post(
                "/v1/messages", json=self._build_payload(req), timeout=req.timeout_seconds
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(
                f"Anthropic request timed out after {req.timeout_seconds}s",
                provider=self.name,
                model_id=req.model_id,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                "Could not reach Anthropic", provider=self.name, model_id=req.model_id, detail=str(exc)
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if resp.status_code >= 400:
            self._raise_for_status(resp, req)

        data = resp.json()
        return self._parse(data, req, latency_ms)

    def _raise_for_status(self, resp: httpx.Response, req: ModelRequest) -> None:
        try:
            body = resp.json()
            message = body.get("error", {}).get("message", resp.text[:500])
            err_type = body.get("error", {}).get("type", "")
        except ValueError:
            message, err_type = resp.text[:500], ""

        kw = {"provider": self.name, "model_id": req.model_id, "detail": message}
        if resp.status_code == 401 or resp.status_code == 403:
            raise ProviderAuthError("Anthropic rejected the API key", **kw)
        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after")
            raise ProviderRateLimited(
                "Anthropic rate limit reached",
                retry_after=float(retry_after) if retry_after else None,
                **kw,
            )
        if resp.status_code in (500, 502, 503, 529):
            raise ProviderUnavailable("Anthropic is unavailable", **kw)
        if err_type == "invalid_request_error":
            raise ProviderInvalidRequest(f"Anthropic rejected the request: {message}", **kw)
        raise ProviderUnavailable(f"Anthropic error {resp.status_code}", **kw)

    def _parse(self, data: dict[str, Any], req: ModelRequest, latency_ms: int) -> ModelResponse:
        text_parts: list[str] = []
        parsed: dict[str, Any] | None = None

        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use" and block.get("name") == STRUCTURED_TOOL_NAME:
                parsed = block.get("input")
                text_parts.append(json.dumps(parsed, indent=2))

        stop_reason = data.get("stop_reason", "stop")
        if stop_reason == "refusal":
            raise ContentFiltered(
                "Anthropic declined to generate this content",
                provider=self.name,
                model_id=req.model_id,
            )

        raw_usage = data.get("usage", {})
        usage = TokenUsage(
            input_tokens=raw_usage.get("input_tokens", 0),
            output_tokens=raw_usage.get("output_tokens", 0),
            cache_read_tokens=raw_usage.get("cache_read_input_tokens", 0),
            cache_write_tokens=raw_usage.get("cache_creation_input_tokens", 0),
        )
        return ModelResponse(
            text="\n".join(p for p in text_parts if p),
            parsed=parsed,
            usage=usage,
            finish_reason=stop_reason,
            model_id=data.get("model", req.model_id),
            provider=self.name,
            latency_ms=latency_ms,
            raw_id=data.get("id", ""),
        )


def _to_anthropic_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Translate a JSON Schema into the subset Anthropic's tool input_schema accepts (ADR-010).

    Anthropic expects a plain object schema at the root. Keywords it does not honour are stripped here rather
    than being sent and silently ignored, so the local validator remains the single source of truth about
    what was actually enforced.
    """
    allowed = {
        "type", "properties", "required", "items", "enum", "description",
        "additionalProperties", "minimum", "maximum", "minItems", "maxItems", "anyOf", "oneOf",
    }

    def clean(node: Any) -> Any:
        """Filter schema *keywords*, never property names.

        The distinction matters: inside `properties`, the keys are user-chosen field names, so applying
        the keyword allowlist there would delete every field in the schema.
        """
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k not in allowed:
                    continue
                if k == "properties" and isinstance(v, dict):
                    out[k] = {name: clean(subschema) for name, subschema in v.items()}
                else:
                    out[k] = clean(v)
            return out
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    cleaned = clean(schema)
    if not isinstance(cleaned, dict) or cleaned.get("type") != "object":
        # Anthropic requires an object root; wrap non-object schemas.
        return {"type": "object", "properties": {"result": cleaned}, "required": ["result"]}
    return cleaned
