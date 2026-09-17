"""Model registry.

Owns model metadata, capability lookup and cost estimation. The router queries this; nothing here knows how
to call a model.

Pricing provenance is first-class (ADR-004): `PriceBook.verified` and `verified_at` travel with every estimate
so an unverified or stale price is visible at the point of use rather than buried in a config file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from app.core.errors import ModelNotFound
from app.pipeline.types import Modality, TokenUsage
from app.providers.base import Capability

CATALOG_PATH = Path(__file__).parent / "catalog.yaml"

# How old a verified price may be before we flag it. Model pricing moved multiple times in 2026, so this is
# deliberately short.
STALENESS_DAYS = 45


@dataclass(frozen=True, slots=True)
class PriceBook:
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    cache_read_per_mtok: float | None = None
    cache_write_per_mtok: float | None = None
    verified: bool = False
    verified_at: date | None = None
    source: str = ""
    note: str = ""
    effective_until: date | None = None

    def is_stale(self, *, today: date | None = None) -> bool:
        today = today or datetime.utcnow().date()
        if self.effective_until and today > self.effective_until:
            return True
        if self.verified_at is None:
            return True
        return (today - self.verified_at).days > STALENESS_DAYS

    def confidence_note(self, *, today: date | None = None) -> str:
        """Human-readable caveat attached to any estimate derived from this price book."""
        if not self.verified:
            return "Price is unverified (from a secondary source); treat cost as indicative only."
        if self.is_stale(today=today):
            return f"Price last verified {self.verified_at}; may be out of date."
        return ""

    def cost_for(self, usage: TokenUsage) -> float:
        cache_read_rate = (
            self.cache_read_per_mtok
            if self.cache_read_per_mtok is not None
            else self.input_per_mtok * 0.1
        )
        cache_write_rate = (
            self.cache_write_per_mtok
            if self.cache_write_per_mtok is not None
            else self.input_per_mtok * 1.25
        )
        return (
            usage.input_tokens * self.input_per_mtok
            + usage.output_tokens * self.output_per_mtok
            + usage.cache_read_tokens * cache_read_rate
            + usage.cache_write_tokens * cache_write_rate
        ) / 1_000_000


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    provider: str
    display_name: str
    family: str
    tier: str
    context_window: int
    max_output_tokens: int
    modalities: frozenset[Modality]
    capabilities: frozenset[Capability]
    quality_index: int
    speed_index: int
    pricing: PriceBook
    available: bool = True

    def supports(self, cap: Capability) -> bool:
        return cap in self.capabilities

    def supports_all(self, caps: set[Capability]) -> bool:
        return caps.issubset(self.capabilities)

    def handles_modalities(self, mods: list[Modality]) -> bool:
        return set(mods).issubset(self.modalities)

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return self.pricing.cost_for(
            TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
        )


@dataclass
class ModelRegistry:
    models: dict[str, ModelInfo] = field(default_factory=dict)
    providers: dict[str, dict[str, str]] = field(default_factory=dict)
    catalog_version: int = 0

    # -- construction ------------------------------------------------------------------
    @classmethod
    def from_catalog(cls, path: Path | None = None) -> ModelRegistry:
        raw = yaml.safe_load((path or CATALOG_PATH).read_text())
        registry = cls(catalog_version=raw.get("catalog_version", 0))
        for p in raw.get("providers", []):
            registry.providers[p["name"]] = p
        for entry in raw.get("models", []):
            info = _parse_model(entry)
            registry.models[info.id] = info
        return registry

    # -- lookup ------------------------------------------------------------------------
    def get(self, model_id: str) -> ModelInfo:
        info = self.models.get(model_id)
        if info is None:
            raise ModelNotFound(f"Unknown model: {model_id}")
        return info

    def find(self, model_id: str) -> ModelInfo | None:
        return self.models.get(model_id)

    def list_models(
        self,
        *,
        providers: set[str] | None = None,
        available_only: bool = True,
        exclude_providers: set[str] | None = None,
    ) -> list[ModelInfo]:
        out: list[ModelInfo] = []
        for m in self.models.values():
            if available_only and not m.available:
                continue
            if providers is not None and m.provider not in providers:
                continue
            if exclude_providers and m.provider in exclude_providers:
                continue
            out.append(m)
        return sorted(out, key=lambda m: (-m.quality_index, m.pricing.output_per_mtok))

    def candidates_for(
        self,
        *,
        required_capabilities: set[Capability],
        required_modalities: list[Modality],
        min_context: int = 0,
        providers: set[str] | None = None,
    ) -> list[ModelInfo]:
        """Hard filter: models that *can* do the job at all. Scoring happens in the router."""
        return [
            m
            for m in self.list_models(providers=providers)
            if m.supports_all(required_capabilities)
            and m.handles_modalities(required_modalities)
            and m.context_window >= min_context
        ]

    def families(self) -> set[str]:
        return {m.family for m in self.models.values()}

    def stale_models(self) -> list[ModelInfo]:
        """Surfaced in the admin UI so price rot is visible rather than silent (ADR-004)."""
        return [m for m in self.models.values() if m.pricing.is_stale() or not m.pricing.verified]


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_model(entry: dict[str, Any]) -> ModelInfo:
    p = entry.get("pricing", {})
    pricing = PriceBook(
        input_per_mtok=float(p.get("input_per_mtok", 0.0)),
        output_per_mtok=float(p.get("output_per_mtok", 0.0)),
        cache_read_per_mtok=(
            float(p["cache_read_per_mtok"]) if p.get("cache_read_per_mtok") is not None else None
        ),
        cache_write_per_mtok=(
            float(p["cache_write_per_mtok"]) if p.get("cache_write_per_mtok") is not None else None
        ),
        verified=bool(p.get("verified", False)),
        verified_at=_parse_date(p.get("verified_at")),
        source=p.get("source", ""),
        note=p.get("note", ""),
        effective_until=_parse_date(p.get("effective_until")),
    )
    return ModelInfo(
        id=entry["id"],
        provider=entry["provider"],
        display_name=entry.get("display_name", entry["id"]),
        family=entry.get("family", entry["provider"]),
        tier=entry.get("tier", "balanced"),
        context_window=int(entry.get("context_window", 128000)),
        max_output_tokens=int(entry.get("max_output_tokens", 4096)),
        modalities=frozenset(Modality(m) for m in entry.get("modalities", ["text"])),
        capabilities=frozenset(Capability(c) for c in entry.get("capabilities", ["text"])),
        quality_index=int(entry.get("quality_index", 50)),
        speed_index=int(entry.get("speed_index", 50)),
        pricing=pricing,
        available=bool(entry.get("available", True)),
    )


_registry: ModelRegistry | None = None


def get_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = ModelRegistry.from_catalog()
    return _registry
