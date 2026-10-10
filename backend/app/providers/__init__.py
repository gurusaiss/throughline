"""Provider registry and factory.

Adding a provider means: write an adapter, add it to `PROVIDER_CLASSES`, and add catalogue rows. No change to
`pipeline` or `evaluation` is required — that is the test of whether the abstraction is real (rule 8).
"""

from __future__ import annotations

import os

from app.core.config import Settings
from app.providers.anthropic import AnthropicProvider
from app.providers.base import Capability, Chunk, Message, ModelRequest, ModelResponse, Provider
from app.providers.google import GoogleProvider
from app.providers.groq import GroqProvider
from app.providers.openai_compatible import (
    OpenAICompatibleProvider,
    OpenAIProvider,
    OpenRouterProvider,
)
from app.providers.scripted import ScriptedProvider

PROVIDER_CLASSES: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "google": GoogleProvider,
    "openrouter": OpenRouterProvider,
    "groq": GroqProvider,
    "scripted": ScriptedProvider,
}

__all__ = [
    "AnthropicProvider",
    "Capability",
    "Chunk",
    "GoogleProvider",
    "GroqProvider",
    "Message",
    "ModelRequest",
    "ModelResponse",
    "OpenAICompatibleProvider",
    "OpenAIProvider",
    "OpenRouterProvider",
    "PROVIDER_CLASSES",
    "Provider",
    "ProviderPool",
    "ScriptedProvider",
]


class ProviderPool:
    """Holds one adapter instance per provider, so HTTP connections are pooled across requests."""

    def __init__(self, settings: Settings, credentials: dict[str, str] | None = None) -> None:
        self._settings = settings
        self._instances: dict[str, Provider] = {}
        self._credentials = credentials or {
            "anthropic": settings.anthropic_api_key,
            "openai": settings.openai_api_key,
            "google": settings.google_api_key,
            "openrouter": settings.openrouter_api_key,
            "groq": settings.groq_api_key,
        }

    def _scripted_allowed(self) -> bool:
        if not self._settings.is_production:
            return True
        return os.getenv("THROUGHLINE_ALLOW_SCRIPTED_PROVIDER") == "1"

    def get(self, provider_name: str) -> Provider | None:
        if provider_name == "scripted" and not self._scripted_allowed():
            return None
        if provider_name in self._instances:
            return self._instances[provider_name]
        cls = PROVIDER_CLASSES.get(provider_name)
        if cls is None:
            return None
        instance = cls(api_key=self._credentials.get(provider_name, ""))
        self._instances[provider_name] = instance
        return instance

    def configured_providers(self) -> set[str]:
        """Names of providers that currently hold a usable credential.

        The router consults this so an unconfigured provider is never selected and then fails at call time
        (`Provider.configured`).
        """
        names: set[str] = set()
        for name in PROVIDER_CLASSES:
            provider = self.get(name)
            if provider is not None and provider.configured:
                names.add(name)
        return names

    async def aclose(self) -> None:
        for provider in self._instances.values():
            await provider.aclose()
        self._instances.clear()
