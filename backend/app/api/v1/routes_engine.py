"""The engine routes: optimize, execute, compare, route.

These are the product. Everything else is around them.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    BudgetDep,
    OrchestratorDep,
    OrgDep,
    ScopeDep,
    SessionDep,
)
from app.api.v1 import mappers, schemas
from app.core.errors import NotFoundError
from app.db.models import Project, Prompt, PromptVersion
from app.evaluation import deterministic
from app.pipeline.orchestrator import ExecutionRequest, ExecutionResult
from app.pipeline.types import OrchestrationMode
from app.services.persistence import save_execution

router = APIRouter(tags=["engine"])


def _mode(value: str) -> OrchestrationMode | None:
    return None if value == "auto" else OrchestrationMode(value)


async def _project_context(session: SessionDep, project_id: str | None, org_id: str) -> str:
    if not project_id:
        return ""
    result = await session.execute(
        select(Project).where(Project.id == project_id, Project.org_id == org_id)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise NotFoundError("Project not found.")
    return project.context


@router.post("/optimize", response_model=schemas.OptimizeResponse)
async def optimize(
    body: schemas.OptimizeRequest,
    orchestrator: OrchestratorDep,
    scope: ScopeDep,
    session: SessionDep,
) -> schemas.OptimizeResponse:
    """Optimise without executing. Costs at most one cheap analysis call, never a generation call."""
    context = await _project_context(session, body.project_id, scope.org_id)
    result = await orchestrator.run(
        ExecutionRequest(
            input=body.input,
            mode=_mode(body.mode),
            project_context=context,
            output_schema=body.output_schema,
            execute=False,
        )
    )
    return mappers.to_optimize_response(result)


@router.post("/execute", response_model=schemas.ExecutionResponse)
async def execute(
    body: schemas.ExecuteRequest,
    orchestrator: OrchestratorDep,
    scope: ScopeDep,
    session: SessionDep,
    org: OrgDep,
    budget: BudgetDep,
    request: Request,
) -> schemas.ExecutionResponse:
    context = await _project_context(session, body.project_id, scope.org_id)
    result = await orchestrator.run(
        ExecutionRequest(
            input=body.input,
            mode=_mode(body.mode),
            project_context=context,
            output_schema=body.output_schema,
            pinned_model_id=body.model_id,
            candidate_count=body.candidate_count,
            temperature=body.temperature,
            max_output_tokens=body.max_output_tokens,
            enable_judge=body.enable_judge,
            max_cost_usd=body.max_cost_usd,
        ),
        budget=budget,
    )
    await save_execution(
        session, result, scope, store_raw=org.store_raw_prompts, project_id=body.project_id
    )

    response = mappers.to_execution_response(result)
    if body.save_as_prompt:
        response.saved_prompt_id = await _save_as_prompt(
            session, result, scope.org_id, scope.user_id, body.project_id, body.save_as_prompt
        )
    return response


async def _save_as_prompt(
    session: AsyncSession,
    result: ExecutionResult,
    org_id: str,
    user_id: str | None,
    project_id: str | None,
    name: str,
) -> str | None:
    """Turn what the optimiser actually produced into a saved, versioned prompt (rule 15).

    This is what `ExecuteRequest.save_as_prompt` is for — it was defined on the schema but never
    implemented, which made the Playground's "save as prompt" button a dead click. Saves the COMPILED
    prompt (system/user text as the optimiser rendered it), not the user's raw input, since that is the
    artefact worth reusing and versioning.
    """
    if result.prompt is None:
        return None

    prompt = Prompt(
        org_id=org_id,
        project_id=project_id,
        created_by=user_id,
        name=name,
        task_type=result.spec.task_type.value if result.spec else "general_qa",
    )
    session.add(prompt)
    await session.flush()

    version = PromptVersion(
        org_id=org_id,
        prompt_id=prompt.id,
        version=1,
        created_by=user_id,
        system_text=result.prompt.system,
        user_text=result.prompt.user,
        output_schema=result.prompt.output_schema,
        optimization_decision=result.prompt.decision.model_dump(),
        notes=f"Saved from execution {result.execution_id}.",
    )
    session.add(version)
    await session.flush()
    prompt.active_version_id = version.id

    return prompt.id


@router.post("/compare", response_model=schemas.CompareResponse)
async def compare(
    body: schemas.CompareRequest,
    orchestrator: OrchestratorDep,
    scope: ScopeDep,
    session: SessionDep,
    org: OrgDep,
    budget: BudgetDep,
) -> schemas.CompareResponse:
    """Run arms side by side so the user can judge for themselves.

    Honest by construction: we show both outputs and the automated checks, and we explicitly decline to
    declare a winner from a single sample (see `CompareResponse.caveat`).
    """
    context = await _project_context(session, body.project_id, scope.org_id)
    arms: list[schemas.ComparisonArm] = []

    if body.compare == "raw_vs_optimized":
        # Arm 1: the user's text, untouched, straight to a model.
        raw_result = await orchestrator.run(
            ExecutionRequest(
                input=body.input, mode=OrchestrationMode.FAST, project_context=""
            ),
            budget=budget,
        )
        # Arm 2: full optimisation.
        opt_result = await orchestrator.run(
            ExecutionRequest(input=body.input, mode=None, project_context=context),
            budget=budget,
        )
        for label, res in (("raw", raw_result), ("optimized", opt_result)):
            arms.append(_to_arm(label, res))
        for res in (raw_result, opt_result):
            await save_execution(session, res, scope, store_raw=org.store_raw_prompts)
    else:
        for model_id in body.model_ids[:4]:
            res = await orchestrator.run(
                ExecutionRequest(
                    input=body.input, project_context=context, pinned_model_id=model_id
                ),
                budget=budget,
            )
            arms.append(_to_arm(model_id, res))
            await save_execution(session, res, scope, store_raw=org.store_raw_prompts)

    return schemas.CompareResponse(
        comparison_id=uuid.uuid4().hex, arms=arms, verdict=_verdict(arms)
    )


def _to_arm(label: str, result: ExecutionResult) -> schemas.ComparisonArm:
    selected = result.selected_candidate
    ev = (
        result.evaluation.for_candidate(selected.id)
        if result.evaluation and selected
        else None
    )
    checks = ev.checks if ev else []
    return schemas.ComparisonArm(
        label=label,
        prompt=mappers.to_prompt(result),
        output=result.output,
        model_id=selected.model_id if selected else "",
        cost_usd=result.cost.total_usd,
        latency_ms=result.trace.total_duration_ms if result.trace else 0,
        checks_passed=len([c for c in checks if c.status.value == "pass"]),
        checks_failed=len([c for c in checks if c.status.value == "fail"]),
        overall=ev.overall if ev else None,
    )


def _verdict(arms: list[schemas.ComparisonArm]) -> str:
    """State what the automated checks found — and nothing beyond it."""
    if len(arms) < 2:
        return "Not enough arms to compare."
    best = max(arms, key=lambda a: (a.checks_failed == 0, a.overall or 0.0))
    failed = [a for a in arms if a.checks_failed > 0]
    if failed and best.checks_failed == 0:
        return (
            f"'{best.label}' passed every automated check; "
            f"{', '.join(a.label for a in failed)} did not."
        )
    cheapest = min(arms, key=lambda a: a.cost_usd)
    if all(a.checks_failed == 0 for a in arms):
        return (
            "All arms passed the automated checks, so this comes down to preference. "
            f"'{cheapest.label}' was cheapest at ${cheapest.cost_usd:.4f}."
        )
    return "Automated checks did not separate these arms clearly. Read both and decide."


@router.post("/route", response_model=schemas.RoutingResponse)
async def route_only(
    body: schemas.OptimizeRequest, orchestrator: OrchestratorDep, scope: ScopeDep
) -> schemas.RoutingResponse:
    """Which model would you pick, and why? Useful for integrators evaluating the router alone."""
    result = await orchestrator.run(
        ExecutionRequest(input=body.input, mode=_mode(body.mode), execute=False)
    )
    routing = mappers.to_routing(result)
    if routing is None:
        raise NotFoundError("No routing plan could be produced for this request.")
    return routing


@router.post("/evaluate", response_model=list[schemas.CheckResponse])
async def evaluate_output(
    body: schemas.OptimizeRequest,
    output: str,
    orchestrator: OrchestratorDep,
    scope: ScopeDep,
) -> list[schemas.CheckResponse]:
    """Run deterministic checks against an output the caller already has.

    Free, no LLM call. Exposed as a standalone utility because it is genuinely useful on its own for
    anyone validating another system's output.
    """
    from app.pipeline.intent import spec_from_signals
    from app.pipeline.signals import extract_signals
    from app.pipeline.types import Candidate

    spec = spec_from_signals(body.input, extract_signals(body.input))
    if body.output_schema is not None:
        spec.output_schema = body.output_schema
    candidate = Candidate(id="external", model_id="external", provider="external", text=output)
    return [
        schemas.CheckResponse(
            name=c.name, status=c.status.value, detail=c.detail, criterion=c.criterion.value
        )
        for c in deterministic.run_all(candidate, spec)
    ]
