"""Cost estimation, usage recording and pre-flight budget enforcement (ADR-011).

Two distinctions this module exists to keep honest:

1. **Estimate vs actual.** An estimate is computed from our catalogue before the call; the actual is computed
   from provider-reported token usage after it. Only the actual is ever billed. `CostEstimate` carries a
   `confidence_note` when the underlying price is unverified or stale (ADR-004).

2. **Pre-flight vs post-hoc.** Budget is checked *before* the provider call. Post-hoc metering can report
   an overspend but cannot prevent it (RESEARCH 7.3), and "usage happens now, payment happens later" is a
   real financial exposure for the operator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import BudgetExceededError
from app.pipeline.types import TokenUsage
from app.registry.registry import ModelInfo, ModelRegistry


@dataclass(slots=True)
class CostEstimate:
    usd: float
    model_id: str
    input_tokens: int
    output_tokens: int
    verified_pricing: bool
    confidence_note: str = ""

    @property
    def is_reliable(self) -> bool:
        return self.verified_pricing and not self.confidence_note


@dataclass(slots=True)
class UsageRecord:
    """One provider call's actual consumption. The billing system of record."""

    model_id: str
    provider: str
    usage: TokenUsage
    cost_usd: float
    latency_ms: int
    purpose: str  # generate | intent | judge — lets us report orchestration overhead separately
    verified_pricing: bool = False
    from_cache: bool = False


@dataclass
class ExecutionCost:
    """Cost breakdown for one end-to-end execution.

    Split by purpose because "what did the answer cost" and "what did deciding cost" are different
    questions, and a platform that hides the second one cannot honestly claim to minimise it.
    """

    records: list[UsageRecord] = field(default_factory=list)

    def add(self, record: UsageRecord) -> None:
        self.records.append(record)

    @property
    def total_usd(self) -> float:
        return round(sum(r.cost_usd for r in self.records), 6)

    @property
    def generation_usd(self) -> float:
        return round(sum(r.cost_usd for r in self.records if r.purpose == "generate"), 6)

    @property
    def orchestration_usd(self) -> float:
        """Everything that was not the answer itself: classification and evaluation."""
        return round(sum(r.cost_usd for r in self.records if r.purpose != "generate"), 6)

    @property
    def orchestration_ratio(self) -> float:
        """Fraction of spend that went on deciding rather than answering.

        A headline internal metric: if this creeps up, the orchestrator is overthinking.
        """
        total = self.total_usd
        return round(self.orchestration_usd / total, 3) if total > 0 else 0.0

    @property
    def total_usage(self) -> TokenUsage:
        out = TokenUsage()
        for r in self.records:
            out = out + r.usage
        return out

    @property
    def any_unverified_pricing(self) -> bool:
        return any(not r.verified_pricing for r in self.records)

    def to_dict(self) -> dict[str, object]:
        usage = self.total_usage
        return {
            "total_usd": self.total_usd,
            "generation_usd": self.generation_usd,
            "orchestration_usd": self.orchestration_usd,
            "orchestration_ratio": self.orchestration_ratio,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "estimates_include_unverified_pricing": self.any_unverified_pricing,
            "calls": len(self.records),
        }


class CostCalculator:
    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry

    def estimate(self, model_id: str, input_tokens: int, output_tokens: int) -> CostEstimate:
        info = self._registry.get(model_id)
        return CostEstimate(
            usd=round(info.estimate_cost(input_tokens, output_tokens), 6),
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            verified_pricing=info.pricing.verified,
            confidence_note=info.pricing.confidence_note(),
        )

    def actual(
        self,
        model_id: str,
        usage: TokenUsage,
        *,
        latency_ms: int,
        purpose: str,
        provider: str = "",
        from_cache: bool = False,
    ) -> UsageRecord:
        info: ModelInfo | None = self._registry.find(model_id)
        if info is None:
            # An unknown model is recorded at zero cost rather than dropped: losing the usage record
            # entirely would under-report consumption, which is the worse error.
            return UsageRecord(
                model_id=model_id, provider=provider, usage=usage, cost_usd=0.0,
                latency_ms=latency_ms, purpose=purpose, verified_pricing=False, from_cache=from_cache,
            )
        cost = 0.0 if from_cache else info.pricing.cost_for(usage)
        return UsageRecord(
            model_id=model_id,
            provider=provider or info.provider,
            usage=usage,
            cost_usd=round(cost, 8),
            latency_ms=latency_ms,
            purpose=purpose,
            verified_pricing=info.pricing.verified,
            from_cache=from_cache,
        )


@dataclass(slots=True)
class Budget:
    limit_usd: float
    spent_usd: float = 0.0
    safety_margin: float = 1.25

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd)

    def check(self, estimate_usd: float) -> None:
        """Raise before spending if the estimate does not fit. Never silently truncates work."""
        needed = estimate_usd * self.safety_margin
        if needed > self.remaining_usd:
            raise BudgetExceededError(
                "This request would exceed the remaining budget.",
                estimated_usd=round(estimate_usd, 6),
                remaining_usd=round(self.remaining_usd, 6),
            )

    def fits(self, estimate_usd: float) -> bool:
        return estimate_usd * self.safety_margin <= self.remaining_usd
