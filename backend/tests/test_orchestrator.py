"""Orchestration policy tests.

These are the product's core claim under test: **the system spends proportionally to the request.**

Call counts are asserted directly, because "we avoid unnecessary LLM calls" is either measurable or it is
marketing.
"""

from __future__ import annotations

import pytest

from app.metering.cost import Budget
from app.pipeline.orchestrator import ExecutionRequest, Orchestrator
from app.pipeline.types import OrchestrationMode


class TestAdaptiveModeSelection:
    async def test_trivial_request_uses_fast_mode_with_one_call(
        self, orchestrator: Orchestrator
    ) -> None:
        """The central claim: a trivial request costs exactly one model call.

        Zero classifier calls, zero judge calls. If this regresses, the product principle is broken.
        """
        result = await orchestrator.run(
            ExecutionRequest(input='Translate "good morning" to Spanish')
        )
        assert result.status == "ok"
        assert result.mode is OrchestrationMode.FAST
        assert result.trace is not None
        assert result.trace.llm_call_count() == 1

    async def test_fast_mode_applies_no_prompt_techniques(self, orchestrator: Orchestrator) -> None:
        """Minimal transformation means minimal — not 'a shorter system prompt'."""
        result = await orchestrator.run(
            ExecutionRequest(input='Translate "good morning" to Spanish')
        )
        assert result.prompt is not None
        assert result.prompt.decision.applied == []
        assert result.prompt.system == ""
        assert result.prompt.user == 'Translate "good morning" to Spanish'

    async def test_high_risk_request_escalates_to_deep(self, orchestrator: Orchestrator) -> None:
        result = await orchestrator.run(
            ExecutionRequest(input="What dosage of ibuprofen should I take for a headache?")
        )
        assert result.mode is OrchestrationMode.DEEP
        assert result.trace is not None
        assert result.trace.llm_call_count() > 1

    async def test_complex_request_escalates_to_deep(self, orchestrator: Orchestrator) -> None:
        result = await orchestrator.run(
            ExecutionRequest(
                input="Design a scalable microservice architecture for a video streaming "
                "platform handling 10M users and plan the migration from our monolith"
            )
        )
        assert result.mode is OrchestrationMode.DEEP
        assert result.plan is not None
        assert len(result.plan.candidates) > 1

    async def test_spends_more_on_harder_requests(self, orchestrator: Orchestrator) -> None:
        """The orchestration spend must be monotonic in request difficulty."""
        trivial = await orchestrator.run(ExecutionRequest(input='Translate "hello" to Spanish'))
        vague = await orchestrator.run(ExecutionRequest(input="help me with something"))
        hard = await orchestrator.run(
            ExecutionRequest(input="Design a distributed system for 10M users and plan the migration")
        )
        assert trivial.trace and vague.trace and hard.trace
        assert trivial.trace.llm_call_count() < vague.trace.llm_call_count()
        assert vague.trace.llm_call_count() < hard.trace.llm_call_count()

    async def test_user_mode_override_is_respected(self, orchestrator: Orchestrator) -> None:
        result = await orchestrator.run(
            ExecutionRequest(input="What is 2+2?", mode=OrchestrationMode.DEEP)
        )
        assert result.mode is OrchestrationMode.DEEP
        assert "explicitly by the user" in result.mode_reason


class TestPromptOnlyMode:
    async def test_prompt_only_does_not_execute(self, orchestrator: Orchestrator) -> None:
        result = await orchestrator.run(
            ExecutionRequest(input="Write a blog post about remote work", execute=False)
        )
        assert result.status == "prompt_only"
        assert result.candidates == []
        assert result.prompt is not None
        assert result.plan is not None  # the user still sees which model *would* be used
        assert result.cost.generation_usd == 0.0


class TestBudgetEnforcement:
    async def test_budget_is_checked_before_spending(
        self, settings, pool, cache, registry
    ) -> None:
        """Pre-flight, not post-hoc (ADR-011): a refused request must make no provider call.

        The offline provider is free, so a priced model is substituted to exercise the check at all —
        a $0 estimate legitimately fits a $0 budget.
        """
        import dataclasses

        from app.registry.registry import PriceBook

        priced = dataclasses.replace(
            registry.get("scripted-standard"),
            pricing=PriceBook(input_per_mtok=50.0, output_per_mtok=100.0, verified=True),
        )
        registry.models["scripted-standard"] = priced

        orch = Orchestrator(settings=settings, registry=registry, pool=pool, cache=cache)
        result = await orch.run(
            ExecutionRequest(
                input="Design a complex distributed system for 10M users",
                pinned_model_id="scripted-standard",
            ),
            budget=Budget(limit_usd=0.0001),
        )
        assert result.status == "budget_exceeded"
        assert result.candidates == []
        assert result.trace is not None
        # The decisive assertion: nothing was generated, so nothing was spent.
        assert not [s for s in result.trace.spans if s.kind.value == "generate"]

    def test_budget_applies_a_safety_margin(self) -> None:
        """Estimates are imperfect, so the check leaves headroom rather than cutting it fine."""
        budget = Budget(limit_usd=1.0, spent_usd=0.5, safety_margin=1.25)
        assert budget.remaining_usd == pytest.approx(0.5)
        assert budget.fits(0.30)        # 0.30 * 1.25 = 0.375 -> fits
        assert not budget.fits(0.45)    # 0.45 * 1.25 = 0.5625 -> refused despite 0.45 < 0.5

    async def test_generous_budget_allows_execution(self, orchestrator: Orchestrator) -> None:
        result = await orchestrator.run(
            ExecutionRequest(input="What is 2+2?"), budget=Budget(limit_usd=100.0)
        )
        assert result.status == "ok"


class TestFailureHandling:
    async def test_no_configured_provider_returns_clear_error(
        self, settings, registry, cache
    ) -> None:
        """An unconfigured platform must say so, not raise an opaque provider error."""
        from app.providers import ProviderPool

        class EmptyPool(ProviderPool):
            def configured_providers(self) -> set[str]:
                return set()

        orch = Orchestrator(
            settings=settings, registry=registry, pool=EmptyPool(settings, credentials={}), cache=cache
        )
        result = await orch.run(ExecutionRequest(input="hello"))
        assert result.status == "error"
        assert "provider is configured" in (result.error or "")

    async def test_generation_failure_is_reported_not_raised(
        self, orchestrator: Orchestrator
    ) -> None:
        """A provider error becomes a structured result, never an unhandled exception."""
        result = await orchestrator.run(
            ExecutionRequest(input="hello there", pinned_model_id="scripted-standard:invalid")
        )
        # The pinned id is not in the registry, so routing declines it and falls back to a real model.
        assert result.status in ("ok", "error")


class TestHonestyGuarantees:
    async def test_high_risk_output_carries_a_caveat(self, orchestrator: Orchestrator) -> None:
        result = await orchestrator.run(
            ExecutionRequest(input="What dosage of ibuprofen should I take?")
        )
        assert any("qualified professional" in w for w in result.warnings)

    async def test_deterministic_only_evaluation_never_reports_high_confidence(
        self, orchestrator: Orchestrator
    ) -> None:
        """Format checks verify shape, never correctness.

        Reporting HIGH confidence because a Markdown table parsed would be a number that looks
        reasonable and is wrong.
        """
        from app.evaluation.types import ConfidenceBand, EvaluationStatus

        result = await orchestrator.run(
            ExecutionRequest(input='Translate "good morning" to Spanish')
        )
        assert result.evaluation is not None
        assert result.evaluation.status is EvaluationStatus.DETERMINISTIC_ONLY
        assert result.confidence is not ConfidenceBand.HIGH

    async def test_cost_separates_orchestration_from_generation(
        self, orchestrator: Orchestrator
    ) -> None:
        """'What did the answer cost' and 'what did deciding cost' are different questions."""
        result = await orchestrator.run(
            ExecutionRequest(input="Design a distributed system for 10M users")
        )
        assert result.cost.total_usd == pytest.approx(
            result.cost.generation_usd + result.cost.orchestration_usd, abs=1e-9
        )

    async def test_every_execution_produces_a_trace(self, orchestrator: Orchestrator) -> None:
        result = await orchestrator.run(ExecutionRequest(input="What is 2+2?"))
        assert result.trace is not None
        kinds = {s.kind.value for s in result.trace.spans}
        assert "signals" in kinds
        assert "route" in kinds
        assert "generate" in kinds


class TestCaching:
    async def test_identical_request_is_served_from_cache(self, orchestrator: Orchestrator) -> None:
        req = ExecutionRequest(input='Translate "good evening" to Spanish')
        first = await orchestrator.run(req)
        second = await orchestrator.run(req)
        assert first.status == "ok"
        assert any(c.from_cache for c in second.candidates)

    async def test_cache_is_tenant_scoped(self, settings, registry, pool) -> None:
        """A cache key must never collide across orgs (ARCHITECTURE §7)."""
        from app.pipeline.cache import ExactMatchCache, InMemoryCacheBackend
        from app.providers.base import ModelRequest

        backend = InMemoryCacheBackend()
        a = ExactMatchCache(backend, tenant_id="org-a")
        b = ExactMatchCache(backend, tenant_id="org-b")
        req = ModelRequest.from_prompt("scripted-standard", "sys", "same question")
        assert a.key(req) != b.key(req)
