"""Provider adapter tests against mocked HTTP.

What matters here is the *boundary contract* (ARCHITECTURE §5): whatever a vendor returns, the core sees
one normalised error taxonomy, one usage shape, and a schema translated into that vendor's dialect. These
tests are the reason `pipeline/` never has to know which provider it is talking to.

Uses respx so no network is touched.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.errors import (
    ContentFiltered,
    ProviderAuthError,
    ProviderInvalidRequest,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.providers.anthropic import AnthropicProvider, _to_anthropic_schema
from app.providers.base import ModelRequest
from app.providers.google import GoogleProvider, _to_gemini_schema
from app.providers.groq import GroqProvider
from app.providers.openai_compatible import (
    OpenAIProvider,
    OpenRouterProvider,
    _to_strict_schema,
)
from app.providers.scripted import ScriptedProvider

SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
    "required": ["name"],
}


def req(model_id: str = "test-model", **kw) -> ModelRequest:
    return ModelRequest.from_prompt(model_id, kw.pop("system", "sys"), kw.pop("user", "hello"), **kw)


# ======================================================================================
# Anthropic
# ======================================================================================
class TestAnthropicAdapter:
    @respx.mock
    async def test_parses_successful_response(self) -> None:
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "msg_1",
                    "model": "claude-sonnet-5",
                    "content": [{"type": "text", "text": "Hello there"}],
                    "stop_reason": "end_turn",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_read_input_tokens": 3,
                        "cache_creation_input_tokens": 2,
                    },
                },
            )
        )
        provider = AnthropicProvider(api_key="test-key")
        resp = await provider.complete(req("claude-sonnet-5"))
        assert resp.text == "Hello there"
        assert resp.usage.input_tokens == 10
        assert resp.usage.cache_read_tokens == 3
        assert resp.usage.cache_write_tokens == 2
        await provider.aclose()

    @respx.mock
    async def test_structured_output_uses_forced_tool_use(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "msg_2",
                    "content": [
                        {"type": "tool_use", "name": "emit_result", "input": {"name": "Ada", "age": 36}}
                    ],
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 5, "output_tokens": 5},
                },
            )

        respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=handler)
        provider = AnthropicProvider(api_key="k")
        resp = await provider.complete(req(output_schema=SCHEMA))

        assert captured["tool_choice"] == {"type": "tool", "name": "emit_result"}
        assert resp.parsed == {"name": "Ada", "age": 36}
        await provider.aclose()

    @respx.mock
    async def test_large_system_prompt_gets_a_cache_breakpoint(self) -> None:
        """Prompt caching pays back after a single read (RESEARCH 7.2), so it must actually be requested."""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"id": "m", "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                      "usage": {"input_tokens": 1, "output_tokens": 1}},
            )

        respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=handler)
        provider = AnthropicProvider(api_key="k")
        await provider.complete(req(system="x" * 3000))
        assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
        await provider.aclose()

    @pytest.mark.parametrize(
        ("status", "body", "expected"),
        [
            (401, {"error": {"message": "bad key"}}, ProviderAuthError),
            (429, {"error": {"message": "slow down"}}, ProviderRateLimited),
            (500, {"error": {"message": "oops"}}, ProviderUnavailable),
            (529, {"error": {"message": "overloaded"}}, ProviderUnavailable),
            (400, {"error": {"type": "invalid_request_error", "message": "bad"}}, ProviderInvalidRequest),
        ],
    )
    @respx.mock
    async def test_errors_are_normalised(self, status: int, body: dict, expected: type) -> None:
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(status, json=body)
        )
        provider = AnthropicProvider(api_key="k")
        with pytest.raises(expected):
            await provider.complete(req())
        await provider.aclose()

    @respx.mock
    async def test_timeout_is_normalised(self) -> None:
        respx.post("https://api.anthropic.com/v1/messages").mock(
            side_effect=httpx.TimeoutException("timed out")
        )
        provider = AnthropicProvider(api_key="k")
        with pytest.raises(ProviderTimeout) as exc:
            await provider.complete(req())
        assert exc.value.retryable  # the orchestrator's retry policy depends on this flag
        await provider.aclose()

    @respx.mock
    async def test_refusal_becomes_content_filtered(self) -> None:
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={"id": "m", "content": [], "stop_reason": "refusal",
                      "usage": {"input_tokens": 1, "output_tokens": 0}},
            )
        )
        provider = AnthropicProvider(api_key="k")
        with pytest.raises(ContentFiltered):
            await provider.complete(req())
        await provider.aclose()

    def test_rate_limit_error_is_retryable_and_others_are_not(self) -> None:
        assert ProviderRateLimited("x").retryable
        assert ProviderUnavailable("x").retryable
        assert not ProviderInvalidRequest("x").retryable
        assert not ProviderAuthError("x").retryable


# ======================================================================================
# OpenAI-compatible
# ======================================================================================
class TestOpenAICompatibleAdapter:
    @respx.mock
    async def test_parses_successful_response(self) -> None:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "cc_1",
                    "model": "gpt-5.6-terra",
                    "choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 4,
                        "prompt_tokens_details": {"cached_tokens": 6},
                    },
                },
            )
        )
        provider = OpenAIProvider(api_key="k")
        resp = await provider.complete(req("gpt-5.6-terra"))
        assert resp.text == "Hi"
        assert resp.usage.input_tokens == 12
        assert resp.usage.cache_read_tokens == 6
        await provider.aclose()

    @respx.mock
    async def test_invalid_json_leaves_parsed_none_for_the_validator(self) -> None:
        """The adapter does not paper over bad JSON — the deterministic validator must see it (ADR-010)."""
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "c",
                    "choices": [{"message": {"content": "{broken"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )
        )
        provider = OpenAIProvider(api_key="k")
        resp = await provider.complete(req(output_schema=SCHEMA))
        assert resp.parsed is None
        assert resp.text == "{broken"
        await provider.aclose()

    @respx.mock
    async def test_content_filter_finish_reason_raises(self) -> None:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"id": "c", "choices": [{"message": {"content": ""},
                                              "finish_reason": "content_filter"}], "usage": {}},
            )
        )
        provider = OpenAIProvider(api_key="k")
        with pytest.raises(ContentFiltered):
            await provider.complete(req())
        await provider.aclose()

    @respx.mock
    async def test_openrouter_uses_its_own_base_url_and_headers(self) -> None:
        route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"id": "c", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                      "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
            )
        )
        provider = OpenRouterProvider(api_key="k")
        await provider.complete(req())
        assert route.called
        assert route.calls[0].request.headers["x-title"] == "Throughline"
        await provider.aclose()

    @respx.mock
    async def test_empty_choices_is_an_error_not_an_empty_answer(self) -> None:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"id": "c", "choices": [], "usage": {}})
        )
        provider = OpenAIProvider(api_key="k")
        with pytest.raises(ProviderUnavailable):
            await provider.complete(req())
        await provider.aclose()


# ======================================================================================
# Google
# ======================================================================================
class TestGoogleAdapter:
    @respx.mock
    async def test_parses_successful_response(self) -> None:
        respx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro:generateContent"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "candidates": [
                        {"content": {"parts": [{"text": "Hello"}]}, "finishReason": "STOP"}
                    ],
                    "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 3},
                },
            )
        )
        provider = GoogleProvider(api_key="k")
        resp = await provider.complete(req("gemini-3.1-pro"))
        assert resp.text == "Hello"
        assert resp.usage.input_tokens == 8
        await provider.aclose()

    @respx.mock
    async def test_safety_block_becomes_content_filtered(self) -> None:
        respx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/g:generateContent"
        ).mock(
            return_value=httpx.Response(
                200, json={"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
            )
        )
        provider = GoogleProvider(api_key="k")
        with pytest.raises(ContentFiltered):
            await provider.complete(req("g"))
        await provider.aclose()


# ======================================================================================
# Groq — the genuine free-tier fallback (MANUAL_STEPS.md §1.2)
# ======================================================================================
class TestGroqAdapter:
    @respx.mock
    async def test_hits_the_real_groq_endpoint(self) -> None:
        """The whole point of the base_url split: this concatenation must land on the real URL, not a
        plausible-looking wrong one. Verified directly against the documented Groq endpoint shape."""
        route = respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "g1",
                    "model": "llama-3.3-70b-versatile",
                    "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                },
            )
        )
        provider = GroqProvider(api_key="gsk_test")
        resp = await provider.complete(req("llama-3.3-70b-versatile"))
        assert route.called
        assert resp.text == "hi"
        await provider.aclose()

    async def test_does_not_claim_vision_or_prompt_caching(self) -> None:
        """Claiming a capability the free-tier text models don't have would route vision/cache-sensitive
        work to a model that can't do it."""
        from app.providers.base import Capability

        provider = GroqProvider(api_key="k")
        assert not provider.supports(Capability.VISION)
        assert not provider.supports(Capability.PROMPT_CACHING)
        assert provider.supports(Capability.TEXT)
        assert provider.supports(Capability.STRUCTURED_OUTPUT)

    async def test_uses_loose_json_mode_not_strict_schema(self) -> None:
        """Groq serves models from several labs with inconsistent strict-schema support (ADR-010); the
        core re-validates locally regardless, so this is a safe default rather than a weaker guarantee."""
        assert GroqProvider.supports_json_schema is False

    @respx.mock
    async def test_rate_limit_is_normalised_like_every_other_provider(self) -> None:
        """The free tier is aggressively rate limited — this is the error path a fallback tier hits often,
        so it must come back as the same ProviderRateLimited the orchestrator already knows how to retry."""
        respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(
                429, json={"error": {"message": "rate limit"}}, headers={"retry-after": "2"}
            )
        )
        provider = GroqProvider(api_key="k")
        with pytest.raises(ProviderRateLimited):
            await provider.complete(req())
        await provider.aclose()


# ======================================================================================
# Schema translation (ADR-010): providers honour different JSON Schema subsets
# ======================================================================================
class TestSchemaTranslation:
    def test_anthropic_strips_unsupported_keywords(self) -> None:
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string", "pattern": "^x", "format": "email"}},
            "required": ["a"],
            "$schema": "http://json-schema.org/draft-07/schema#",
        }
        out = _to_anthropic_schema(schema)
        assert "$schema" not in out
        assert "pattern" not in out["properties"]["a"]
        assert out["properties"]["a"]["type"] == "string"

    def test_anthropic_wraps_non_object_root(self) -> None:
        """Anthropic requires an object root; a bare array schema must not be sent as-is."""
        out = _to_anthropic_schema({"type": "array", "items": {"type": "string"}})
        assert out["type"] == "object"
        assert "result" in out["properties"]

    def test_openai_strict_mode_tightens_schema(self) -> None:
        out = _to_strict_schema(SCHEMA)
        assert out["additionalProperties"] is False
        # Strict mode requires every property in `required`.
        assert set(out["required"]) == {"name", "age"}

    def test_gemini_uppercases_types(self) -> None:
        out = _to_gemini_schema(SCHEMA)
        assert out["type"] == "OBJECT"
        assert out["properties"]["name"]["type"] == "STRING"
        assert out["properties"]["age"]["type"] == "INTEGER"

    def test_translation_never_loosens_the_contract(self) -> None:
        """Whatever the adapter sends, the ORIGINAL schema is what we validate against locally."""
        for translate in (_to_anthropic_schema, _to_strict_schema, _to_gemini_schema):
            translated = translate(SCHEMA)
            assert translated is not SCHEMA  # no in-place mutation of the caller's schema
        assert SCHEMA["required"] == ["name"]


# ======================================================================================
# Offline double
# ======================================================================================
class TestScriptedProvider:
    async def test_is_deterministic(self) -> None:
        """Determinism is what lets tests assert on orchestration behaviour without flakiness."""
        provider = ScriptedProvider()
        a = await provider.complete(req("scripted-standard"))
        b = await provider.complete(req("scripted-standard"))
        assert a.text == b.text

    async def test_produces_schema_valid_json(self) -> None:
        import json

        provider = ScriptedProvider()
        resp = await provider.complete(req("scripted-standard", output_schema=SCHEMA))
        payload = json.loads(resp.text)
        assert set(payload) >= {"name"}

    async def test_can_be_asked_to_produce_broken_json(self) -> None:
        """Needed to exercise the repair path without depending on a real model misbehaving."""
        import json

        provider = ScriptedProvider()
        resp = await provider.complete(req("scripted-standard:badjson", output_schema=SCHEMA))
        with pytest.raises(json.JSONDecodeError):
            json.loads(resp.text)

    async def test_records_calls_for_assertions(self) -> None:
        provider = ScriptedProvider()
        await provider.complete(req("scripted-standard"))
        await provider.complete(req("scripted-fast"))
        assert [c.model_id for c in provider.calls] == ["scripted-standard", "scripted-fast"]


class TestProviderPool:
    def test_unconfigured_providers_are_excluded(self, settings) -> None:  # noqa: ANN001
        """The router filters on this so a missing key fails at routing, where a fallback still exists."""
        from app.providers import ProviderPool

        pool = ProviderPool(settings, credentials={})
        configured = pool.configured_providers()
        assert "scripted" in configured
        assert "anthropic" not in configured

    def test_configured_provider_is_included(self, settings) -> None:  # noqa: ANN001
        from app.providers import ProviderPool

        pool = ProviderPool(settings, credentials={"anthropic": "sk-test"})
        assert "anthropic" in pool.configured_providers()

    def test_scripted_provider_is_blocked_in_production(self) -> None:
        """A test double must never silently serve production traffic (rule 60)."""
        from app.core.config import Settings
        from app.providers import ProviderPool

        prod = Settings(environment="production", secret_key="x" * 40)
        assert ProviderPool(prod, credentials={}).get("scripted") is None
