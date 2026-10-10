"""Groq adapter.

Groq matters to this product for a reason the other adapters don't share: it has a genuine **free tier**
— no credit card, real token limits, served over the standard OpenAI-compatible Chat Completions contract
(confirmed live during this build: https://api.groq.com/openai/v1, GROQ_API_KEY). That makes it the
fallback tier referenced in `MANUAL_STEPS.md` — a tenant with zero paid provider keys configured still
gets real model output, not just the offline echo provider, as long as `GROQ_API_KEY` is set.

Most behaviour is inherited from `OpenAICompatibleProvider` (rule 59: no duplicated provider logic); this
file only overrides what is genuinely different about Groq.
"""

from __future__ import annotations

from app.providers.base import Capability
from app.providers.openai_compatible import OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"
    # The parent's complete() always posts to f"{base_url}/v1/chat/completions". Groq's real endpoint is
    # https://api.groq.com/openai/v1/chat/completions, so the base must stop one path segment short of
    # the OpenAI-compatible prefix for that concatenation to land correctly.
    default_base_url = "https://api.groq.com/openai"

    # Groq serves open-weight models (Llama, DeepSeek, Kimi, GPT-OSS) from several different upstream
    # labs behind one API. Strict JSON-schema mode (`response_format.json_schema.strict`) is an
    # OpenAI-specific feature that these models honour inconsistently — some reject the parameter
    # outright with a 400. The looser `json_object` mode is accepted everywhere Groq serves, and the core
    # re-validates every structured output against the full schema locally regardless of what a provider
    # promises (ADR-010), so nothing is lost by not claiming a guarantee Groq can't make for every model
    # it hosts.
    supports_json_schema = False

    def supports(self, cap: Capability) -> bool:
        return cap in {
            Capability.TEXT,
            Capability.STRUCTURED_OUTPUT,
            Capability.TOOL_USE,
            Capability.STREAMING,
            Capability.REASONING,
            Capability.LONG_CONTEXT,
            # Deliberately NOT VISION: Groq's free-tier text models used here are text-only; a vision
            # model would need its own catalogue entry and capability set, added when one is configured.
            # Deliberately NOT PROMPT_CACHING: Groq does not offer Anthropic-style explicit prompt
            # caching, so claiming the capability would make the router expect a cache discount that
            # never materialises in the cost accounting.
        }

    # Rate limiting and error mapping (401/403/429/5xx, content_filter) are fully inherited from
    # OpenAICompatibleProvider._raise_for_status, which already honours a `Retry-After` header — Groq
    # returns one on its free-tier 429s. The free tier is aggressively rate limited (on the order of 30
    # requests/minute and ~14,400/day at the account level, confirmed via Groq's own documentation during
    # this build), which is exactly why this adapter is positioned as a *fallback* tier for the router
    # (see RoutingPreferences and the router's provider scoring) rather than a primary choice for
    # production traffic at any real concurrency.
