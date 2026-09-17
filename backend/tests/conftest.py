"""Shared test fixtures.

Every test in this suite runs with **no network and no API keys**, using the scripted provider. That is a
deliberate property: CI must be able to verify orchestration behaviour (how many calls did we make, did the
right stage get skipped) without spending money or depending on a vendor being up.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.pipeline.cache import ExactMatchCache, InMemoryCacheBackend
from app.pipeline.orchestrator import Orchestrator
from app.providers import ProviderPool
from app.registry.registry import ModelRegistry


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        secret_key="test-secret-key-that-is-long-enough-for-validation",
        anthropic_api_key="",
        openai_api_key="",
        google_api_key="",
        openrouter_api_key="",
    )


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry.from_catalog()


@pytest.fixture
def pool(settings: Settings) -> ProviderPool:
    # No credentials -> only the scripted provider is configured.
    return ProviderPool(settings, credentials={})


@pytest.fixture
def cache() -> ExactMatchCache:
    return ExactMatchCache(InMemoryCacheBackend(), tenant_id="test-org")


@pytest.fixture
def orchestrator(
    settings: Settings, registry: ModelRegistry, pool: ProviderPool, cache: ExactMatchCache
) -> Orchestrator:
    return Orchestrator(settings=settings, registry=registry, pool=pool, cache=cache)
