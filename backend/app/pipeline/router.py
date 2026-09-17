"""Model router (ADR-007).

Two stages, deliberately separated:

1. **Hard filter** — can this model do the job at all? Capabilities, modalities, context window, provider
   configured. A model that fails here is never selected, so a capability gap surfaces at routing time where
   a fallback is still possible, not at call time where it is an error.
2. **Score** — among the models that *can*, which *should*? A weighted trade-off across quality, cost and
   latency, where the weights come from the request's own needs.

Every decision carries a human-readable reason, because the "why this model?" surface needs it and because a
routing rule nobody can explain is a routing rule nobody can fix.

Why rules and not a learned router: RouteLLM's classifier was trained on Chatbot Arena preference data
(RESEARCH 4.1). We have none until we have traffic. `RoutingPolicy` is the seam a learned policy slots into
once the feedback and evaluation tables have enough rows to train on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.core.errors import NoViableModelError
from app.pipeline.taxonomy import get_profile
from app.pipeline.types import (
    Complexity,
    ExecutionSpec,
    ModelChoice,
    OrchestrationMode,
    RiskLevel,
    RoutingPlan,
    RoutingStrategy,
)
from app.providers.base import Capability
from app.registry.registry import ModelInfo, ModelRegistry

# Rough characters-per-token used only for pre-flight estimates. Real usage always comes from the provider
# (ADR-004 / ADR-011) — this is for budgeting and routing, never for billing.
CHARS_PER_TOKEN = 4

# Minimum quality index for a model to be trusted to apply a rubric. Below this, judge scores correlate
# poorly enough with human judgement that they add noise rather than signal.
JUDGE_MIN_QUALITY = 70


@dataclass(slots=True)
class RoutingPreferences:
    """User- or plan-level preferences. All optional; sensible defaults for an unopinionated caller."""

    quality_weight: float = 1.0
    cost_weight: float = 1.0
    latency_weight: float = 0.5
    max_cost_usd: float | None = None
    pinned_model_id: str | None = None
    allowed_providers: set[str] | None = None
    excluded_providers: set[str] = field(default_factory=set)
    candidate_count: int = 1
    require_cross_family_candidates: bool = True

    @classmethod
    def for_mode(cls, mode: OrchestrationMode, **kw: object) -> RoutingPreferences:
        """Preference presets per orchestration mode.

        FAST leans on cost and speed because it is reserved for requests where quality headroom is not the
        binding constraint; DEEP/EXPERT invert that.
        """
        presets = {
            OrchestrationMode.FAST: dict(quality_weight=0.6, cost_weight=1.4, latency_weight=1.0),
            OrchestrationMode.BALANCED: dict(quality_weight=1.0, cost_weight=1.0, latency_weight=0.5),
            OrchestrationMode.DEEP: dict(quality_weight=1.6, cost_weight=0.6, latency_weight=0.2),
            OrchestrationMode.EXPERT: dict(quality_weight=2.0, cost_weight=0.4, latency_weight=0.1),
        }
        merged: dict[str, object] = {**presets[mode], **kw}
        return cls(**merged)  # type: ignore[arg-type]


class RoutingPolicy(Protocol):
    """Seam for swapping in a learned router later without touching callers (ADR-007)."""

    def route(
        self,
        spec: ExecutionSpec,
        *,
        mode: OrchestrationMode,
        prefs: RoutingPreferences,
        available_providers: set[str],
    ) -> RoutingPlan: ...


class RuleBasedRouter:
    """Explainable scoring router. The MVP policy."""

    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry

    # -- public ------------------------------------------------------------------------
    def route(
        self,
        spec: ExecutionSpec,
        *,
        mode: OrchestrationMode = OrchestrationMode.BALANCED,
        prefs: RoutingPreferences | None = None,
        available_providers: set[str] | None = None,
    ) -> RoutingPlan:
        prefs = prefs or RoutingPreferences.for_mode(mode)
        rejected: list[dict[str, str]] = []

        viable = self._hard_filter(spec, prefs, available_providers, rejected)
        if not viable:
            raise NoViableModelError(
                "No configured model satisfies this request's requirements",
                detail=(
                    "; ".join(f"{r['model']}: {r['reason']}" for r in rejected[:6])
                    or "no providers configured"
                ),
            )

        if prefs.pinned_model_id:
            pinned = next((m for m in viable if m.id == prefs.pinned_model_id), None)
            if pinned is not None:
                return self._single_plan(pinned, spec, "Model pinned by user.", rejected)
            rejected.append(
                {"model": prefs.pinned_model_id, "reason": "pinned model cannot satisfy requirements"}
            )

        scored = sorted(
            ((self._score(m, spec, prefs), m) for m in viable), key=lambda p: p[0], reverse=True
        )
        strategy = self._choose_strategy(spec, mode, prefs, len(scored))

        if strategy is RoutingStrategy.SINGLE:
            score, best = scored[0]
            return self._single_plan(best, spec, self._reason(best, spec, score), rejected)

        if strategy is RoutingStrategy.FALLBACK:
            return self._fallback_plan(scored, spec, rejected)

        return self._multi_plan(scored, spec, prefs, strategy, rejected)

    # -- stage 1: hard filter ----------------------------------------------------------
    def _hard_filter(
        self,
        spec: ExecutionSpec,
        prefs: RoutingPreferences,
        available_providers: set[str] | None,
        rejected: list[dict[str, str]],
    ) -> list[ModelInfo]:
        required = self._required_capabilities(spec)
        min_context = self._estimated_input_tokens(spec) + 2000  # headroom for the compiled prompt

        viable: list[ModelInfo] = []
        for m in self._registry.list_models():
            if available_providers is not None and m.provider not in available_providers:
                rejected.append({"model": m.id, "reason": f"provider {m.provider} not configured"})
                continue
            if prefs.allowed_providers is not None and m.provider not in prefs.allowed_providers:
                rejected.append({"model": m.id, "reason": "provider not permitted by preferences"})
                continue
            if m.provider in prefs.excluded_providers:
                rejected.append({"model": m.id, "reason": "provider excluded by preferences"})
                continue
            missing = required - m.capabilities
            if missing:
                rejected.append(
                    {"model": m.id, "reason": f"missing {', '.join(sorted(c.value for c in missing))}"}
                )
                continue
            if not m.handles_modalities(spec.required_modalities):
                rejected.append({"model": m.id, "reason": "does not handle required modality"})
                continue
            if m.context_window < min_context:
                rejected.append(
                    {"model": m.id, "reason": f"context window {m.context_window} < {min_context}"}
                )
                continue
            viable.append(m)
        return viable

    def _required_capabilities(self, spec: ExecutionSpec) -> set[Capability]:
        caps: set[Capability] = {Capability.TEXT}
        profile = get_profile(spec.task_type)
        caps |= set(profile.required_capabilities)
        if spec.requires_structured_output:
            caps.add(Capability.STRUCTURED_OUTPUT)
        if spec.reasoning_depth.value == "deep" or spec.complexity is Complexity.VERY_HIGH:
            caps.add(Capability.REASONING)
        # `needs_tools` alone does not force WEB_SEARCH: the MVP has no tool execution, so requiring it
        # would filter out every model and fail a request we can still answer (with a stated caveat).
        return caps

    # -- stage 2: scoring --------------------------------------------------------------
    def _score(self, m: ModelInfo, spec: ExecutionSpec, prefs: RoutingPreferences) -> float:
        quality = m.quality_index / 100.0
        speed = m.speed_index / 100.0
        cost = self._estimated_cost(m, spec)

        # Normalise cost against a reference so the term is scale-free across a catalogue spanning
        # $0.20/MTok to $50/MTok. Cheaper than reference scores above 0.5.
        cost_score = 1.0 / (1.0 + cost / 0.02)

        # Demand for quality rises with complexity and risk — this is where the request's own nature,
        # rather than a global preference, drives the trade-off.
        quality_demand = 1.0 + 0.35 * spec.complexity.rank + 0.3 * spec.risk_level.rank

        score = (
            prefs.quality_weight * quality_demand * quality
            + prefs.cost_weight * cost_score
            + prefs.latency_weight * speed
        )

        if prefs.max_cost_usd is not None and cost > prefs.max_cost_usd:
            score -= 10.0  # effectively disqualifying, but still ranked so the reason is inspectable

        # An unverified price is a weak negative: we may be routing on a wrong number (RESEARCH 3.2).
        if not m.pricing.verified:
            score -= 0.05
        return round(score, 4)

    def _estimated_input_tokens(self, spec: ExecutionSpec) -> int:
        return max(1, len(spec.raw_input) // CHARS_PER_TOKEN) + 400  # + compiled scaffolding

    def _estimated_output_tokens(self, spec: ExecutionSpec) -> int:
        return get_profile(spec.task_type).typical_output_tokens

    def _estimated_cost(self, m: ModelInfo, spec: ExecutionSpec) -> float:
        return m.estimate_cost(self._estimated_input_tokens(spec), self._estimated_output_tokens(spec))

    def _estimated_latency(self, m: ModelInfo, spec: ExecutionSpec) -> int:
        """Crude: output tokens divided by a speed proxy, plus fixed overhead. Labelled an estimate."""
        out = self._estimated_output_tokens(spec)
        tokens_per_second = 8 + (m.speed_index * 1.2)
        return int(400 + (out / tokens_per_second) * 1000)

    # -- strategy ----------------------------------------------------------------------
    def _choose_strategy(
        self,
        spec: ExecutionSpec,
        mode: OrchestrationMode,
        prefs: RoutingPreferences,
        n_viable: int,
    ) -> RoutingStrategy:
        if prefs.pinned_model_id:
            return RoutingStrategy.SINGLE
        if n_viable < 2:
            return RoutingStrategy.SINGLE
        if mode is OrchestrationMode.FAST:
            return RoutingStrategy.SINGLE
        if mode is OrchestrationMode.BALANCED:
            # A fallback costs nothing unless the primary fails, so it is free insurance on
            # high-risk work. Elsewhere it adds routing complexity for no expected benefit.
            return (
                RoutingStrategy.FALLBACK
                if spec.risk_level.rank >= RiskLevel.MEDIUM.rank
                else RoutingStrategy.SINGLE
            )
        if mode is OrchestrationMode.DEEP:
            return RoutingStrategy.PARALLEL if prefs.candidate_count > 1 else RoutingStrategy.FALLBACK
        return RoutingStrategy.ENSEMBLE

    # -- plan builders -----------------------------------------------------------------
    def _single_plan(
        self, m: ModelInfo, spec: ExecutionSpec, reason: str, rejected: list[dict[str, str]]
    ) -> RoutingPlan:
        return RoutingPlan(
            strategy=RoutingStrategy.SINGLE,
            selections=[
                ModelChoice(model_id=m.id, provider=m.provider, role="primary", reason=reason)
            ],
            reason=reason,
            estimated_cost_usd=round(self._estimated_cost(m, spec), 6),
            estimated_latency_ms=self._estimated_latency(m, spec),
            rejected=rejected[:10],
        )

    def _fallback_plan(
        self,
        scored: list[tuple[float, ModelInfo]],
        spec: ExecutionSpec,
        rejected: list[dict[str, str]],
    ) -> RoutingPlan:
        primary_score, primary = scored[0]
        # Prefer a fallback from a different provider: the failure mode a fallback exists to survive is
        # usually a provider-wide outage or rate limit, which a same-provider fallback shares.
        fallback = next(
            (m for _, m in scored[1:] if m.provider != primary.provider),
            scored[1][1] if len(scored) > 1 else None,
        )
        selections = [
            ModelChoice(
                model_id=primary.id, provider=primary.provider, role="primary",
                score=primary_score, reason=self._reason(primary, spec, primary_score),
            )
        ]
        if fallback is not None:
            selections.append(
                ModelChoice(
                    model_id=fallback.id, provider=fallback.provider, role="fallback",
                    reason=f"Fallback on {fallback.provider} if {primary.provider} fails.",
                )
            )
        return RoutingPlan(
            strategy=RoutingStrategy.FALLBACK,
            selections=selections,
            reason=(
                f"{self._reason(primary, spec, primary_score)} A fallback is held in reserve because this "
                f"request is {spec.risk_level.value} risk."
            ),
            estimated_cost_usd=round(self._estimated_cost(primary, spec), 6),
            estimated_latency_ms=self._estimated_latency(primary, spec),
            rejected=rejected[:10],
        )

    def _multi_plan(
        self,
        scored: list[tuple[float, ModelInfo]],
        spec: ExecutionSpec,
        prefs: RoutingPreferences,
        strategy: RoutingStrategy,
        rejected: list[dict[str, str]],
    ) -> RoutingPlan:
        wanted = max(2, min(prefs.candidate_count, len(scored)))
        chosen: list[ModelInfo] = []
        seen_families: set[str] = set()

        # Cross-family diversity first. Two candidates from one family tend to agree with each other and
        # with the judge for reasons that have nothing to do with being right, which makes the comparison
        # look more informative than it is (RESEARCH 5.1, self-preference).
        if prefs.require_cross_family_candidates:
            for _, m in scored:
                if len(chosen) >= wanted:
                    break
                if m.family not in seen_families:
                    chosen.append(m)
                    seen_families.add(m.family)
        for _, m in scored:
            if len(chosen) >= wanted:
                break
            if m not in chosen:
                chosen.append(m)

        selections = [
            ModelChoice(
                model_id=m.id, provider=m.provider,
                role="primary" if i == 0 else "candidate",
                score=next(s for s, mm in scored if mm.id == m.id),
                reason=self._reason(m, spec, next(s for s, mm in scored if mm.id == m.id)),
            )
            for i, m in enumerate(chosen)
        ]
        total_cost = sum(self._estimated_cost(m, spec) for m in chosen)
        families = sorted({m.family for m in chosen})
        return RoutingPlan(
            strategy=strategy,
            selections=selections,
            reason=(
                f"Generating {len(chosen)} candidates across {len(families)} model families "
                f"({', '.join(families)}) because this request is {spec.complexity.value} complexity; "
                f"they are compared before one is returned."
            ),
            estimated_cost_usd=round(total_cost, 6),
            # Parallel fan-out, so wall-clock is the slowest candidate, not the sum (rule 20).
            estimated_latency_ms=max(self._estimated_latency(m, spec) for m in chosen),
            rejected=rejected[:10],
        )

    def _reason(self, m: ModelInfo, spec: ExecutionSpec, score: float) -> str:
        bits: list[str] = []
        if spec.complexity.rank >= Complexity.HIGH.rank:
            bits.append("high complexity favours a stronger model")
        elif spec.complexity.rank <= Complexity.LOW.rank:
            bits.append("a simple request does not need a frontier model")
        if spec.requires_structured_output:
            bits.append("structured output required")
        if spec.risk_level.rank >= RiskLevel.MEDIUM.rank:
            bits.append(f"{spec.risk_level.value} risk")
        cost = self._estimated_cost(m, spec)
        bits.append(f"estimated ${cost:.4f}")
        if not m.pricing.verified:
            bits.append("cost estimate unverified")
        return f"{m.display_name} selected: " + ", ".join(bits) + "."

    # -- judge selection ---------------------------------------------------------------
    def select_judge(
        self,
        generator_families: set[str],
        *,
        available_providers: set[str] | None = None,
        preferred_model_id: str | None = None,
    ) -> ModelInfo | None:
        """Pick a judge, preferring a family that did not generate any candidate.

        Self-preference bias is documented and measurable (RESEARCH 5.1): a judge inflates scores for its
        own family. When no cross-family judge is available we still judge, but the caller records
        `self_preference_risk=True` so the score is read with that caveat rather than trusted blindly.
        """
        pool = [
            m for m in self._registry.list_models()
            if (available_providers is None or m.provider in available_providers)
            and Capability.STRUCTURED_OUTPUT in m.capabilities
        ]
        if not pool:
            return None
        if preferred_model_id:
            pinned = next((m for m in pool if m.id == preferred_model_id), None)
            if pinned is not None:
                return pinned

        cross = [m for m in pool if m.family not in generator_families]
        candidates = cross or pool

        # Judging is a scoring task against a rubric, not a generation task: it needs competence, not
        # frontier capability. So we take the CHEAPEST model clearing a quality bar rather than the best
        # one available — otherwise evaluation costs more than the work it evaluates, which is exactly the
        # waste this product exists to remove (RESEARCH 5.2: judges are not free).
        strong_enough = [m for m in candidates if m.quality_index >= JUDGE_MIN_QUALITY] or candidates
        return sorted(
            strong_enough, key=lambda m: (m.pricing.output_per_mtok, -m.quality_index)
        )[0]
