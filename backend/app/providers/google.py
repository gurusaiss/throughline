"""Google Gemini adapter (generateContent).

**Verification note:** `ai.google.dev` was egress-blocked during research, so this targets the documented
`v1beta/models/{model}:generateContent` contract. Operator must confirm before production use
(RESEARCH §10.2).
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


class GoogleProvider(Provider):
    name = "google"

    def __init__(self, api_key: str = "", base_url: str = "https://generativelanguage.googleapis.com") -> None:
        super().__init__(api_key=api_key, base_url=base_url or "https://generativelanguage.googleapis.com")
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

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url, headers={"content-type": "application/json"}
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _build_payload(self, req: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contents": [
                {"role": "user" if m.role == "user" else "model", "parts": [{"text": m.content}]}
                for m in req.messages
            ],
            "generationConfig": {"maxOutputTokens": req.max_output_tokens},
        }
        if req.system:
            payload["systemInstruction"] = {"parts": [{"text": req.system}]}
        if req.temperature is not None:
            payload["generationConfig"]["temperature"] = req.temperature
        if req.stop_sequences:
            payload["generationConfig"]["stopSequences"] = req.stop_sequences
        if req.output_schema is not None:
            payload["generationConfig"]["responseMimeType"] = "application/json"
            payload["generationConfig"]["responseSchema"] = _to_gemini_schema(req.output_schema)
        return payload

    async def complete(self, req: ModelRequest) -> ModelResponse:
        client = self._get_client()
        started = time.perf_counter()
        url = f"/v1beta/models/{req.model_id}:generateContent"
        try:
            resp = await client.post(
                url,
                json=self._build_payload(req),
                params={"key": self._api_key},
                timeout=req.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(
                f"Google request timed out after {req.timeout_seconds}s",
                provider=self.name,
                model_id=req.model_id,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                "Could not reach Google", provider=self.name, model_id=req.model_id, detail=str(exc)
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if resp.status_code >= 400:
            self._raise_for_status(resp, req)
        return self._parse(resp.json(), req, latency_ms)

    def _raise_for_status(self, resp: httpx.Response, req: ModelRequest) -> None:
        try:
            message = resp.json().get("error", {}).get("message", resp.text[:500])
        except ValueError:
            message = resp.text[:500]
        kw = {"provider": self.name, "model_id": req.model_id, "detail": message}
        if resp.status_code in (401, 403):
            raise ProviderAuthError("Google rejected the API key", **kw)
        if resp.status_code == 429:
            raise ProviderRateLimited("Google rate limit reached", **kw)
        if resp.status_code >= 500:
            raise ProviderUnavailable("Google is unavailable", **kw)
        raise ProviderInvalidRequest(f"Google rejected the request: {message}", **kw)

    def _parse(self, data: dict[str, Any], req: ModelRequest, latency_ms: int) -> ModelResponse:
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            if reason:
                raise ContentFiltered(
                    f"Google blocked this prompt ({reason})", provider=self.name, model_id=req.model_id
                )
            raise ProviderUnavailable(
                "Google returned no candidates", provider=self.name, model_id=req.model_id
            )

        cand = candidates[0]
        finish_reason = cand.get("finishReason", "STOP")
        if finish_reason == "SAFETY":
            raise ContentFiltered(
                "Google filtered this content", provider=self.name, model_id=req.model_id
            )

        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
        parsed: dict[str, Any] | None = None
        if req.output_schema is not None and text:
            try:
                loaded = json.loads(text)
                parsed = loaded if isinstance(loaded, dict) else None
            except json.JSONDecodeError:
                parsed = None

        raw_usage = data.get("usageMetadata") or {}
        usage = TokenUsage(
            input_tokens=raw_usage.get("promptTokenCount", 0),
            output_tokens=raw_usage.get("candidatesTokenCount", 0),
            cache_read_tokens=raw_usage.get("cachedContentTokenCount", 0),
        )
        return ModelResponse(
            text=text,
            parsed=parsed,
            usage=usage,
            finish_reason=finish_reason.lower(),
            model_id=req.model_id,
            provider=self.name,
            latency_ms=latency_ms,
            raw_id=data.get("responseId", ""),
        )


def _to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini uses an OpenAPI-flavoured schema with UPPERCASE type names and a narrow keyword set (ADR-010)."""
    type_map = {
        "string": "STRING", "integer": "INTEGER", "number": "NUMBER",
        "boolean": "BOOLEAN", "array": "ARRAY", "object": "OBJECT",
    }
    allowed = {"type", "properties", "required", "items", "enum", "description", "nullable"}

    def walk(node: Any) -> Any:
        """Filter schema *keywords*, never property names.

        Inside `properties` the keys are user-chosen field names; applying the keyword allowlist there
        would delete every field in the schema.
        """
        if isinstance(node, list):
            return [walk(v) for v in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k not in allowed:
                continue
            if k == "properties" and isinstance(v, dict):
                out[k] = {name: walk(subschema) for name, subschema in v.items()}
            elif k == "type" and isinstance(v, str):
                out[k] = type_map.get(v, v)
            else:
                out[k] = walk(v)
        return out

    return walk(schema)
