"""Domain -> API response mapping.

Kept separate from the routers so response shaping (including the honesty caveats) is defined once and
tested independently of HTTP.
"""

from __future__ import annotations

from app.api.v1 import schemas
from app.evaluation.types import EvaluationStatus
from app.pipeline.orchestrator import ExecutionResult
from app.registry.registry import ModelInfo


def to_why(result: ExecutionResult) -> schemas.WhyResponse:
    spec = result.spec
    decision = result.prompt.decision if result.prompt else None
    return schemas.WhyResponse(
        mode=result.mode.value,
        mode_reason=result.mode_reason,
        task_type=spec.task_type.value if spec else "unknown",
        complexity=spec.complexity.value if spec else "unknown",
        ambiguity=spec.ambiguity.value if spec else "unknown",
        risk_level=spec.risk_level.value if spec else "unknown",
        applied=[
            schemas.AppliedTechniqueResponse(
                technique=a.technique.value, reason=a.reason, trigger=a.trigger
            )
            for a in (decision.applied if decision else [])
        ],
        skipped=(decision.skipped if decision else []),
        rationale=(decision.rationale if decision else ""),
        signal_confidence=result.signals.confidence if result.signals else 0.0,
        inferred_fields={k: v.value for k, v in (spec.provenance.items() if spec else [])},
    )


def to_prompt(result: ExecutionResult) -> schemas.CompiledPromptResponse:
    p = result.prompt
    return schemas.CompiledPromptResponse(
        system=p.system if p else "",
        user=p.user if p else "",
        output_schema=p.output_schema if p else None,
    )


def to_routing(result: ExecutionResult) -> schemas.RoutingResponse | None:
    plan = result.plan
    if plan is None:
        return None
    return schemas.RoutingResponse(
        strategy=plan.strategy.value,
        selections=[
            schemas.ModelChoiceResponse(
                model_id=s.model_id, provider=s.provider, role=s.role, reason=s.reason
            )
            for s in plan.selections
        ],
        reason=plan.reason,
        estimated_cost_usd=plan.estimated_cost_usd,
        estimated_latency_ms=plan.estimated_latency_ms,
    )


def to_candidates(result: ExecutionResult) -> list[schemas.CandidateResponse]:
    report = result.evaluation
    out: list[schemas.CandidateResponse] = []
    for c in result.candidates:
        ev = report.for_candidate(c.id) if report else None
        out.append(
            schemas.CandidateResponse(
                id=c.id,
                model_id=c.model_id,
                provider=c.provider,
                text=c.text,
                cost_usd=c.cost_usd,
                latency_ms=c.latency_ms,
                from_cache=c.from_cache,
                error=c.error,
                checks=[
                    schemas.CheckResponse(
                        name=ck.name,
                        status=ck.status.value,
                        detail=ck.detail,
                        criterion=ck.criterion.value,
                    )
                    for ck in (ev.checks if ev else [])
                ],
                scores=[
                    schemas.ScoreResponse(
                        criterion=s.criterion.value,
                        score=s.score,
                        source=s.source.value,
                        reasoning=s.reasoning,
                    )
                    for s in (ev.scores if ev else [])
                ],
                overall=ev.overall if ev else None,
            )
        )
    return out


def to_evaluation(result: ExecutionResult) -> schemas.EvaluationResponse | None:
    report = result.evaluation
    if report is None:
        return None
    mit = report.mitigations
    return schemas.EvaluationResponse(
        status=report.status.value,
        selected_candidate_id=report.selected_candidate_id,
        selection_reason=report.selection_reason,
        model_agreement=report.model_agreement,
        judge_agreement=report.judge_agreement,
        mitigations=(
            schemas.MitigationsResponse(
                order_randomized=mit.order_randomized,
                model_identity_hidden=mit.model_identity_hidden,
                judge_family=mit.judge_family,
                self_preference_risk=mit.self_preference_risk,
                length_delta_chars=mit.length_delta_chars,
            )
            if mit
            else None
        ),
        cost_usd=report.cost_usd,
    )


def to_cost(result: ExecutionResult) -> schemas.CostResponse:
    usage = result.cost.total_usage
    return schemas.CostResponse(
        total_usd=result.cost.total_usd,
        generation_usd=result.cost.generation_usd,
        orchestration_usd=result.cost.orchestration_usd,
        orchestration_ratio=result.cost.orchestration_ratio,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        llm_calls=result.trace.llm_call_count() if result.trace else 0,
        is_estimate=False,  # derived from provider-reported usage
        includes_unverified_pricing=result.cost.any_unverified_pricing,
    )


def to_execution_response(result: ExecutionResult) -> schemas.ExecutionResponse:
    return schemas.ExecutionResponse(
        execution_id=result.execution_id,
        status=result.status,
        output=result.output,
        confidence=result.confidence.value,
        why=to_why(result),
        prompt=to_prompt(result),
        routing=to_routing(result),
        candidates=to_candidates(result),
        evaluation=to_evaluation(result),
        cost=to_cost(result),
        latency_ms=result.trace.total_duration_ms if result.trace else 0,
        warnings=result.warnings,
        error=result.error,
    )


def to_optimize_response(result: ExecutionResult) -> schemas.OptimizeResponse:
    return schemas.OptimizeResponse(
        status=result.status,
        prompt=to_prompt(result),
        why=to_why(result),
        routing=to_routing(result),
        estimated_cost_usd=result.plan.estimated_cost_usd if result.plan else 0.0,
    )


def to_model_response(info: ModelInfo, *, configured: bool) -> schemas.ModelResponse:
    p = info.pricing
    return schemas.ModelResponse(
        id=info.id,
        provider=info.provider,
        display_name=info.display_name,
        family=info.family,
        tier=info.tier,
        context_window=info.context_window,
        max_output_tokens=info.max_output_tokens,
        modalities=sorted(m.value for m in info.modalities),
        capabilities=sorted(c.value for c in info.capabilities),
        quality_index=info.quality_index,
        speed_index=info.speed_index,
        pricing=schemas.PricingResponse(
            input_per_mtok=p.input_per_mtok,
            output_per_mtok=p.output_per_mtok,
            cache_read_per_mtok=p.cache_read_per_mtok,
            verified=p.verified,
            verified_at=p.verified_at.isoformat() if p.verified_at else None,
            source=p.source,
            # Surfaced rather than hidden: stale pricing is the failure documented in RESEARCH 3.2.
            staleness_warning=p.confidence_note(),
        ),
        available=info.available,
        configured=configured,
    )


def evaluation_is_advisory(result: ExecutionResult) -> bool:
    return bool(
        result.evaluation
        and result.evaluation.status
        in (EvaluationStatus.DETERMINISTIC_ONLY, EvaluationStatus.UNAVAILABLE)
    )
