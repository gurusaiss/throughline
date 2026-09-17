"""Adaptive orchestration — the core differentiator (PRODUCT §5, ARCHITECTURE §3).

This module decides *how much machinery a request deserves*, then spends exactly that much.

The load-bearing property is the FAST gate: a trivial request reaches a single provider call having made
**zero** extra LLM calls, because the decision to skip was made by free heuristics (ADR-003). A system that
spends a classifier call to decide not to spend classifier calls has not solved the problem.

Escalation is bounded twice over — by attempt count and by remaining budget — so no path can loop.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.core.errors import (
    BudgetExceededError,
    NoViableModelError,
    ProviderError,
    ThroughlineError,
)
from app.evaluation import aggregate, deterministic
from app.evaluation.judge import LLMJudge
from app.evaluation.types import (
    ConfidenceBand,
    EvaluationReport,
    EvaluationStatus,
)
from app.metering.cost import Budget, CostCalculator, ExecutionCost
from app.observability.trace import SpanKind, SpanStatus, Trace
from app.pipeline.cache import ExactMatchCache
from app.pipeline.intent import IntentAnalyzer, spec_from_signals
from app.pipeline.optimizer import PromptOptimizer
from app.pipeline.router import RoutingPreferences, RuleBasedRouter
from app.pipeline.signals import extract_signals
from app.pipeline.taxonomy import get_profile
from app.pipeline.types import (
    Ambiguity,
    Candidate,
    CompiledPrompt,
    Complexity,
    ExecutionSpec,
    ModelChoice,
    OrchestrationMode,
    RequestSignals,
    RiskLevel,
    RoutingPlan,
    RoutingStrategy,
)
from app.providers import ProviderPool
from app.providers.base import Capability, ModelRequest, Provider
from app.registry.registry import ModelInfo, ModelRegistry

# Minimum quality index for a model to be trusted with structured classification. Below this the intent
# fields come back noisy enough that heuristics would have been better and cheaper.
INTENT_MIN_QUALITY = 60


@dataclass(slots=True)
class ExecutionRequest:
    """What the caller asked for. Explicit user choices always override policy."""

    input: str
    mode: OrchestrationMode | None = None       # None => choose adaptively
    project_context: str = ""
    output_schema: dict[str, Any] | None = None
    pinned_model_id: str | None = None
    candidate_count: int | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    execute: bool = True                        # False => "prompt only" mode
    enable_judge: bool | None = None
    max_cost_usd: float | None = None
    allowed_providers: set[str] | None = None


@dataclass
class ExecutionResult:
    execution_id: str
    status: str = "ok"
    output: str = ""
    signals: RequestSignals | None = None
    spec: ExecutionSpec | None = None
    prompt: CompiledPrompt | None = None
    plan: RoutingPlan | None = None
    candidates: list[Candidate] = field(default_factory=list)
    evaluation: EvaluationReport | None = None
    confidence: ConfidenceBand = ConfidenceBand.LOW
    mode: OrchestrationMode = OrchestrationMode.BALANCED
    mode_reason: str = ""
    cost: ExecutionCost = field(default_factory=ExecutionCost)
    trace: Trace | None = None
    from_cache: bool = False
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def selected_candidate(self) -> Candidate | None:
        if self.evaluation and self.evaluation.selected_candidate_id:
            for c in self.candidates:
                if c.id == self.evaluation.selected_candidate_id:
                    return c
        return next((c for c in self.candidates if c.ok), None)


class Orchestrator:
    def __init__(
        self,
        *,
        settings: Settings,
        registry: ModelRegistry,
        pool: ProviderPool,
        cache: ExactMatchCache | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._pool = pool
        self._router = RuleBasedRouter(registry)
        self._optimizer = PromptOptimizer()
        self._costs = CostCalculator(registry)
        self._cache = cache

    # ==================================================================================
    # Entry point
    # ==================================================================================
    async def run(self, req: ExecutionRequest, *, budget: Budget | None = None) -> ExecutionResult:
        trace = Trace()
        result = ExecutionResult(execution_id=uuid.uuid4().hex, trace=trace)
        available = self._pool.configured_providers()

        if not available:
            result.status = "error"
            result.error = (
                "No model provider is configured. Add an API key for at least one provider."
            )
            return result

        try:
            with trace.span("orchestration", SpanKind.ORCHESTRATION) as root:
                # -- 1. signals: free, deterministic, always ---------------------------
                with trace.span("extract_signals", SpanKind.SIGNALS) as s:
                    signals = extract_signals(req.input)
                    result.signals = signals
                    s.set(**{
                        "task": signals.likely_task.value,
                        "complexity": signals.complexity.value,
                        "ambiguity": signals.ambiguity.value,
                        "risk": signals.risk.value,
                        "confidence": signals.confidence,
                    })

                # -- 2. choose the mode ------------------------------------------------
                mode, mode_reason = self._choose_mode(signals, req)
                result.mode, result.mode_reason = mode, mode_reason
                root.set(mode=mode.value, mode_reason=mode_reason)

                # -- 3. understand: heuristics only, or pay for a classifier call -------
                spec = await self._build_spec(req, signals, mode, trace, result, available)
                if req.output_schema is not None:
                    spec.output_schema = req.output_schema
                result.spec = spec

                # -- 4. optimise -------------------------------------------------------
                with trace.span("optimize", SpanKind.OPTIMIZE) as s:
                    prompt = self._optimizer.optimize(spec, project_context=req.project_context)
                    result.prompt = prompt
                    s.set(
                        techniques=prompt.decision.technique_names,
                        skipped=len(prompt.decision.skipped),
                        system_chars=len(prompt.system),
                        user_chars=len(prompt.user),
                    )

                # -- 5. route ----------------------------------------------------------
                with trace.span("route", SpanKind.ROUTE) as s:
                    prefs = self._preferences(req, mode, spec)
                    plan = self._router.route(
                        spec, mode=mode, prefs=prefs, available_providers=available
                    )
                    result.plan = plan
                    s.set(
                        strategy=plan.strategy.value,
                        models=[c.model_id for c in plan.selections],
                        estimated_cost_usd=plan.estimated_cost_usd,
                    )

                # "Prompt only" stops here — the user wanted the prompt, not the answer.
                if not req.execute:
                    result.status = "prompt_only"
                    return result

                # -- 6. budget pre-flight (ADR-011) ------------------------------------
                if budget is not None:
                    budget.check(plan.estimated_cost_usd)

                # -- 7. generate -------------------------------------------------------
                candidates = await self._generate(prompt, plan, spec, req, trace, result)
                result.candidates = candidates

                if not any(c.ok for c in candidates):
                    result.status = "error"
                    result.error = self._first_error(candidates) or "All models failed to respond."
                    return result

                # -- 8. evaluate -------------------------------------------------------
                report = await self._evaluate(
                    candidates, spec, mode, req, trace, result, available, budget
                )
                result.evaluation = report
                result.confidence = aggregate.confidence_band(report, spec)

                selected = result.selected_candidate
                result.output = selected.text if selected else ""

                # -- 9. bounded repair on deterministic failure ------------------------
                if selected is not None and spec.output_schema is not None:
                    ev = report.for_candidate(selected.id)
                    if ev is not None and not ev.deterministically_valid:
                        repaired = await self._attempt_repair(
                            selected, spec, prompt, trace, result, req
                        )
                        if repaired is not None:
                            result.candidates.append(repaired)
                            result.output = repaired.text
                            result.warnings.append(
                                "The first response did not match the required schema; it was "
                                "automatically corrected and re-checked."
                            )

                self._add_honesty_warnings(result, spec)
                root.set(
                    llm_calls=trace.llm_call_count(),
                    total_cost_usd=result.cost.total_usd,
                    orchestration_ratio=result.cost.orchestration_ratio,
                )

        except BudgetExceededError as exc:
            result.status = "budget_exceeded"
            result.error = exc.message
            result.warnings.append(
                f"Estimated ${exc.estimated_usd:.4f} but only ${exc.remaining_usd:.4f} remains."
            )
        except NoViableModelError as exc:
            result.status = "error"
            result.error = f"{exc.message}. {exc.detail}"
        except ThroughlineError as exc:
            result.status = "error"
            result.error = exc.message

        return result

    # ==================================================================================
    # Mode selection — the heart of the policy
    # ==================================================================================
    def _choose_mode(
        self, signals: RequestSignals, req: ExecutionRequest
    ) -> tuple[OrchestrationMode, str]:
        if req.mode is not None:
            return req.mode, "Mode selected explicitly by the user."

        # FAST gate. A *composite* condition, not a bare confidence threshold: classification confidence
        # matters little when the request is trivially simple, and matters a lot when it is not.
        trivially_simple = (
            signals.complexity is Complexity.TRIVIAL
            and signals.ambiguity.rank <= Ambiguity.LOW.rank
            and signals.risk is RiskLevel.LOW
            and not signals.needs_tools
            and not signals.needs_multimodal
        )
        confidently_simple = (
            signals.confidence >= self._settings.fast_path_confidence_threshold
            and signals.complexity.rank <= Complexity.LOW.rank
            and signals.ambiguity.rank <= Ambiguity.LOW.rank
            and signals.risk is RiskLevel.LOW
            and not signals.needs_tools
        )

        if trivially_simple:
            return (
                OrchestrationMode.FAST,
                "The request is simple and unambiguous, so it was sent straight to a model with no "
                "extra analysis.",
            )
        if confidently_simple:
            return (
                OrchestrationMode.FAST,
                f"The request was confidently classified as {signals.likely_task.value} and needs no "
                f"clarification, so no analysis step was needed.",
            )

        # High complexity or high stakes justify comparing candidates. Complexity alone is enough: a
        # well-specified hard problem is still a hard problem, and comparing candidates is where the
        # quality gain on hard problems actually comes from.
        if signals.risk is RiskLevel.HIGH:
            return (
                OrchestrationMode.DEEP,
                f"This is a {signals.risk.value}-risk topic, so several responses were generated and "
                f"compared before answering.",
            )
        if signals.complexity.rank >= Complexity.HIGH.rank:
            return (
                OrchestrationMode.DEEP,
                f"This is {signals.complexity.value} complexity, so several responses were generated and "
                f"compared before answering.",
            )

        return (
            OrchestrationMode.BALANCED,
            "The request needed clarifying and structuring, but not multiple competing answers.",
        )

    # ==================================================================================
    # Stages
    # ==================================================================================
    async def _build_spec(
        self,
        req: ExecutionRequest,
        signals: RequestSignals,
        mode: OrchestrationMode,
        trace: Trace,
        result: ExecutionResult,
        available: set[str],
    ) -> ExecutionSpec:
        """FAST mode uses heuristics only — this is where the saved LLM call actually happens."""
        if mode is OrchestrationMode.FAST:
            return spec_from_signals(req.input, signals)

        analyzer_model = self._pick_intent_model(available)
        if analyzer_model is None:
            result.warnings.append(
                "No suitable model was available for request analysis; heuristics were used instead."
            )
            return spec_from_signals(req.input, signals)

        provider = self._pool.get(analyzer_model.provider)
        if provider is None:
            return spec_from_signals(req.input, signals)

        with trace.span("intent_analysis", SpanKind.INTENT) as s:
            s.set(model_id=analyzer_model.id)
            try:
                analyzer = IntentAnalyzer(provider, analyzer_model.id)
                spec, meta = await analyzer.analyze(req.input, signals)
                record = self._costs.actual(
                    analyzer_model.id, meta["usage"], latency_ms=meta["latency_ms"],
                    purpose="intent", provider=meta["provider"],
                )
                result.cost.add(record)
                s.cost_usd = record.cost_usd
                s.set(task=spec.task_type.value, complexity=spec.complexity.value)
                return spec
            except (ProviderError, ThroughlineError) as exc:
                # A failed classifier degrades the pipeline; it must never break it.
                s.set(fallback="heuristics")
                result.warnings.append(
                    "Request analysis was unavailable, so a simpler analysis was used."
                )
                s.error = str(exc)
                return spec_from_signals(req.input, signals)

    def _pick_intent_model(self, available: set[str]) -> ModelInfo | None:
        """Cheapest capable model for classification.

        Never a frontier model: filling in eight enum fields is a labelling job, and paying frontier prices
        for it is exactly the waste this product exists to remove.
        """
        candidates = [
            m
            for m in self._registry.list_models(providers=available)
            if m.supports(Capability.STRUCTURED_OUTPUT) and m.quality_index >= INTENT_MIN_QUALITY
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda m: (m.pricing.input_per_mtok, -m.speed_index))[0]

    def _preferences(
        self, req: ExecutionRequest, mode: OrchestrationMode, spec: ExecutionSpec
    ) -> RoutingPreferences:
        count = req.candidate_count
        if count is None:
            count = (
                min(self._settings.max_candidates, 3)
                if mode in (OrchestrationMode.DEEP, OrchestrationMode.EXPERT)
                else 1
            )
        return RoutingPreferences.for_mode(
            mode,
            pinned_model_id=req.pinned_model_id,
            candidate_count=count,
            max_cost_usd=req.max_cost_usd,
            allowed_providers=req.allowed_providers,
        )

    async def _generate(
        self,
        prompt: CompiledPrompt,
        plan: RoutingPlan,
        spec: ExecutionSpec,
        req: ExecutionRequest,
        trace: Trace,
        result: ExecutionResult,
    ) -> list[Candidate]:
        """Generate candidates, in parallel when there is more than one (rule 20)."""
        targets = plan.candidates or ([plan.primary] if plan.primary else [])
        targets = [t for t in targets if t is not None]

        if plan.strategy is RoutingStrategy.FALLBACK:
            return [await self._generate_with_fallback(prompt, plan, spec, req, trace, result)]

        tasks = [
            self._generate_one(choice, prompt, spec, req, trace, result) for choice in targets
        ]
        return list(await asyncio.gather(*tasks))

    async def _generate_one(
        self,
        choice: ModelChoice,
        prompt: CompiledPrompt,
        spec: ExecutionSpec,
        req: ExecutionRequest,
        trace: Trace,
        result: ExecutionResult,
    ) -> Candidate:
        candidate_id = uuid.uuid4().hex[:12]
        provider = self._pool.get(choice.provider)
        if provider is None:
            return Candidate(
                id=candidate_id, model_id=choice.model_id, provider=choice.provider,
                text="", error=f"Provider {choice.provider} is not configured.",
            )

        info = self._registry.find(choice.model_id)
        max_out = req.max_output_tokens or min(
            get_profile(spec.task_type).typical_output_tokens * 3,
            info.max_output_tokens if info else 4096,
        )

        model_req = ModelRequest.from_prompt(
            model_id=choice.model_id,
            system=prompt.system,
            user=prompt.user,
            output_schema=prompt.output_schema,
            max_output_tokens=max_out,
            temperature=req.temperature,
            timeout_seconds=self._settings.provider_timeout_seconds,
            cacheable_prefix_chars=prompt.cacheable_prefix_chars,
        )

        # Exact-match cache only (ADR-005): zero correctness risk, real savings on retries and comparisons.
        cache_key = None
        if self._cache is not None and self._settings.enable_exact_cache:
            cache_key = self._cache.key(model_req)
            cached = await self._cache.get(cache_key)
            if cached is not None:
                with trace.span("cache_hit", SpanKind.CACHE) as s:
                    s.set(model_id=choice.model_id)
                return Candidate(
                    id=candidate_id, model_id=choice.model_id, provider=choice.provider,
                    text=cached, from_cache=True, latency_ms=0,
                )

        with trace.span(f"generate:{choice.model_id}", SpanKind.GENERATE) as s:
            s.set(model_id=choice.model_id, provider=choice.provider, role=choice.role)
            try:
                resp = await self._call_with_retry(provider, model_req)
            except ThroughlineError as exc:
                s.status = SpanStatus.ERROR
                s.error = exc.message
                return Candidate(
                    id=candidate_id, model_id=choice.model_id, provider=choice.provider,
                    text="", error=exc.message,
                )

            record = self._costs.actual(
                choice.model_id, resp.usage, latency_ms=resp.latency_ms,
                purpose="generate", provider=resp.provider,
            )
            result.cost.add(record)
            s.cost_usd = record.cost_usd
            s.set(
                output_tokens=resp.usage.output_tokens,
                input_tokens=resp.usage.input_tokens,
                cache_read_tokens=resp.usage.cache_read_tokens,
            )

            if cache_key is not None and self._cache is not None:
                await self._cache.set(cache_key, resp.text)

            return Candidate(
                id=candidate_id, model_id=choice.model_id, provider=choice.provider,
                text=resp.text, usage=resp.usage, cost_usd=record.cost_usd,
                latency_ms=resp.latency_ms, finish_reason=resp.finish_reason,
            )

    async def _generate_with_fallback(
        self,
        prompt: CompiledPrompt,
        plan: RoutingPlan,
        spec: ExecutionSpec,
        req: ExecutionRequest,
        trace: Trace,
        result: ExecutionResult,
    ) -> Candidate:
        for choice in plan.selections:
            candidate = await self._generate_one(choice, prompt, spec, req, trace, result)
            if candidate.ok:
                if choice.role == "fallback":
                    result.warnings.append(
                        f"The primary model was unavailable; {choice.provider} answered instead."
                    )
                return candidate
        return candidate  # last failure, for the error message

    async def _call_with_retry(self, provider: Provider, model_req: ModelRequest) -> Any:
        """Bounded retry with backoff. Only retryable errors are retried (rule 49)."""
        attempts = self._settings.provider_max_retries
        last: ThroughlineError | None = None
        for attempt in range(attempts + 1):
            try:
                return await provider.complete(model_req)
            except ProviderError as exc:
                last = exc
                if not exc.retryable or attempt == attempts:
                    raise
                delay = min(2.0 ** attempt, 8.0)
                retry_after = getattr(exc, "retry_after", None)
                await asyncio.sleep(retry_after if retry_after else delay)
        if last:
            raise last
        raise ProviderError("Provider call failed")

    async def _evaluate(
        self,
        candidates: list[Candidate],
        spec: ExecutionSpec,
        mode: OrchestrationMode,
        req: ExecutionRequest,
        trace: Trace,
        result: ExecutionResult,
        available: set[str],
        budget: Budget | None,
    ) -> EvaluationReport:
        # Deterministic checks always run: free, instant, authoritative.
        with trace.span("deterministic_checks", SpanKind.EVALUATE) as s:
            det = {c.id: deterministic.run_all(c, spec) for c in candidates}
            s.set(
                checks=sum(len(v) for v in det.values()),
                failures=sum(len([x for x in v if x.is_blocking]) for v in det.values()),
            )

        if not self._should_judge(spec, mode, req, candidates, budget):
            return aggregate.build_report(
                candidates, det, None, spec, status=EvaluationStatus.DETERMINISTIC_ONLY
            )

        generator_families = {
            info.family for c in candidates if (info := self._registry.find(c.model_id)) is not None
        }
        judge_info = self._router.select_judge(
            generator_families,
            available_providers=available,
            preferred_model_id=self._settings.judge_model_id or None,
        )
        if judge_info is None:
            return aggregate.build_report(
                candidates, det, None, spec, status=EvaluationStatus.DETERMINISTIC_ONLY
            )
        judge_provider = self._pool.get(judge_info.provider)
        if judge_provider is None:
            return aggregate.build_report(
                candidates, det, None, spec, status=EvaluationStatus.DETERMINISTIC_ONLY
            )

        with trace.span("judge", SpanKind.JUDGE) as s:
            s.set(judge_model=judge_info.id, judge_family=judge_info.family)
            try:
                judge = LLMJudge(judge_provider, judge_info.id, judge_info.family)
                evals, mitigations, meta = await judge.judge(
                    candidates, spec, generator_families=generator_families
                )
                record = self._costs.actual(
                    judge_info.id, meta["usage"], latency_ms=meta["latency_ms"],
                    purpose="judge", provider=meta["provider"],
                )
                result.cost.add(record)
                s.cost_usd = record.cost_usd
                if mitigations.self_preference_risk:
                    result.warnings.append(
                        "The evaluating model shares a family with a model that produced a candidate, "
                        "which can bias the comparison in its favour."
                    )
                return aggregate.build_report(
                    candidates, det, evals, spec,
                    mitigations=mitigations, status=EvaluationStatus.COMPLETE,
                    cost_usd=record.cost_usd, latency_ms=meta["latency_ms"],
                )
            except (ProviderError, ThroughlineError) as exc:
                # Evaluation failure must never block the answer (rule 49).
                s.error = str(exc)
                result.warnings.append(
                    "Automated quality review was unavailable for this response."
                )
                return aggregate.build_report(
                    candidates, det, None, spec,
                    status=EvaluationStatus.UNAVAILABLE, error=str(exc),
                )

    def _should_judge(
        self,
        spec: ExecutionSpec,
        mode: OrchestrationMode,
        req: ExecutionRequest,
        candidates: list[Candidate],
        budget: Budget | None,
    ) -> bool:
        """Judging is sampled, not universal (RESEARCH 5.2 — judges are not free)."""
        if req.enable_judge is not None:
            return req.enable_judge
        if not self._settings.enable_judge:
            return False
        usable = [c for c in candidates if c.ok]
        if not usable:
            return False
        # With multiple candidates a judge is the entire point: something must choose.
        if len(usable) > 1:
            return True
        # With one candidate, judging is a quality check — worth it for risk, not for a haiku.
        if mode is OrchestrationMode.FAST:
            return False
        if spec.risk_level.rank >= RiskLevel.MEDIUM.rank:
            return True
        return mode in (OrchestrationMode.DEEP, OrchestrationMode.EXPERT)

    async def _attempt_repair(
        self,
        broken: Candidate,
        spec: ExecutionSpec,
        prompt: CompiledPrompt,
        trace: Trace,
        result: ExecutionResult,
        req: ExecutionRequest,
    ) -> Candidate | None:
        """One bounded repair attempt for structured output (ADR-010). Never loops."""
        if self._settings.max_repair_attempts < 1:
            return None
        provider = self._pool.get(broken.provider)
        if provider is None:
            return None

        checks = deterministic.run_all(broken, spec)
        problems = "; ".join(c.detail for c in checks if c.is_blocking and c.detail)[:600]
        if not problems:
            return None

        repair_prompt = (
            f"{prompt.user}\n\n"
            f"## Correction required\n"
            f"A previous attempt produced output that failed validation:\n{problems}\n\n"
            f"Return corrected output that satisfies the schema exactly. Output only the JSON."
        )
        with trace.span("repair", SpanKind.GENERATE) as s:
            s.set(model_id=broken.model_id, reason="schema_validation_failed")
            try:
                resp = await self._call_with_retry(
                    provider,
                    ModelRequest.from_prompt(
                        model_id=broken.model_id,
                        system=prompt.system,
                        user=repair_prompt,
                        output_schema=spec.output_schema,
                        temperature=0.0,
                        max_output_tokens=req.max_output_tokens or 4096,
                        timeout_seconds=self._settings.provider_timeout_seconds,
                    ),
                )
            except ThroughlineError:
                return None

            record = self._costs.actual(
                broken.model_id, resp.usage, latency_ms=resp.latency_ms,
                purpose="generate", provider=resp.provider,
            )
            result.cost.add(record)
            s.cost_usd = record.cost_usd

            repaired = Candidate(
                id=uuid.uuid4().hex[:12], model_id=broken.model_id, provider=broken.provider,
                text=resp.text, usage=resp.usage, cost_usd=record.cost_usd,
                latency_ms=resp.latency_ms,
            )
            # Only accept the repair if it actually fixed the problem.
            if [c for c in deterministic.run_all(repaired, spec) if c.is_blocking]:
                return None
            return repaired

    # ==================================================================================
    # Honesty
    # ==================================================================================
    def _add_honesty_warnings(self, result: ExecutionResult, spec: ExecutionSpec) -> None:
        """Surface the caveats the product is obliged to surface (PRODUCT §7)."""
        if spec.risk_level is RiskLevel.HIGH:
            result.warnings.append(
                "This topic can have serious consequences. Treat the response as a starting point and "
                "verify it with a qualified professional."
            )
        if spec.needs_current_info:
            result.warnings.append(
                "This request depends on current information. The model answered from training data and "
                "may be out of date."
            )
        if result.cost.any_unverified_pricing:
            result.warnings.append(
                "Cost figures are estimates based on unverified provider pricing."
            )
        if result.evaluation and result.evaluation.status is EvaluationStatus.DETERMINISTIC_ONLY:
            result.warnings.append(
                "Only automated format and constraint checks were run; content quality was not reviewed."
            )

    def _first_error(self, candidates: list[Candidate]) -> str | None:
        for c in candidates:
            if c.error:
                return c.error
        return None
