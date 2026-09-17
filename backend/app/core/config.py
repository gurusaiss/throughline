"""Application configuration.

Secrets come from the environment only. Nothing here has a real credential as a default, and the app refuses
to start in production with a placeholder secret (see `validate_production`).
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET_SENTINEL = "dev-only-insecure-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    app_name: str = "Throughline"
    api_prefix: str = "/api/v1"

    # --- security ---
    secret_key: str = DEV_SECRET_SENTINEL
    encryption_key: str = ""  # Fernet key for provider-credential encryption at rest
    access_token_ttl_minutes: int = 60
    refresh_token_ttl_days: int = 30
    # Stored as a raw comma-separated string: pydantic-settings attempts JSON-decoding of `list[str]`
    # fields sourced from the environment/`.env`, which rejects a plain "http://a,http://b" value before
    # our own validator ever runs. Splitting it ourselves via the `cors_origins` property sidesteps that.
    cors_origins_raw: str = "http://localhost:3000"

    # --- datastores ---
    database_url: str = "postgresql+asyncpg://throughline:throughline@localhost:5432/throughline"
    redis_url: str = "redis://localhost:6379/0"

    # --- provider credentials (platform-level defaults; tenants may bring their own) ---
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""
    openrouter_api_key: str = ""

    # --- orchestration policy ---
    default_mode: str = "auto"
    fast_path_confidence_threshold: float = 0.72
    max_candidates: int = 3
    max_repair_attempts: int = 1
    provider_timeout_seconds: float = 90.0
    provider_max_retries: int = 2

    # --- cost controls ---
    default_org_monthly_budget_usd: float = 25.0
    budget_safety_margin: float = 1.25  # estimates are imperfect; leave headroom (ADR-011)
    enable_exact_cache: bool = True
    cache_ttl_seconds: int = 3600
    enable_semantic_cache: bool = False  # shadow mode only when enabled (ADR-005)

    # --- evaluation policy ---
    enable_judge: bool = True
    judge_sampling_rate: float = 1.0  # fraction of eligible executions that get a judge pass
    judge_model_id: str = ""  # empty -> router picks a cross-family judge

    # --- privacy ---
    log_raw_prompts: bool = False  # off by default (rule 28)
    default_retention_days: int = 90

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate_production(self) -> list[str]:
        """Return blocking misconfigurations. Called at startup; refuses to boot in production."""
        problems: list[str] = []
        if not self.is_production:
            return problems
        if self.secret_key == DEV_SECRET_SENTINEL or len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be set to a strong value (>=32 chars) in production")
        if not self.encryption_key:
            problems.append("ENCRYPTION_KEY must be set in production to encrypt provider credentials")
        if self.debug:
            problems.append("DEBUG must be false in production")
        if self.log_raw_prompts:
            problems.append("LOG_RAW_PROMPTS must be false in production unless explicitly reviewed")
        if any(o == "*" for o in self.cors_origins):
            problems.append("CORS_ORIGINS must not be '*' in production")
        return problems

    def generate_dev_secret(self) -> str:
        return secrets.token_urlsafe(48)


@lru_cache
def get_settings() -> Settings:
    return Settings()
