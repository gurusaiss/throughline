"""Execution history, feedback and analytics."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select

from app.api.deps import OrgDep, ScopeDep, SessionDep
from app.api.v1 import schemas
from app.core.errors import NotFoundError
from app.db.models import Evaluation, Execution, Feedback
from app.services.persistence import get_execution_scoped, preview_of, record_audit

router = APIRouter(tags=["history"])


@router.get("/executions", response_model=list[schemas.ExecutionSummary])
async def list_executions(
    session: SessionDep,
    scope: ScopeDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    task_type: str | None = None,
) -> list[schemas.ExecutionSummary]:
    stmt = select(Execution).where(Execution.org_id == scope.org_id)
    if task_type:
        stmt = stmt.where(Execution.task_type == task_type)
    stmt = stmt.order_by(Execution.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return [
        schemas.ExecutionSummary(
            id=e.id,
            created_at=e.created_at.isoformat(),
            status=e.status,
            mode=e.mode,
            task_type=e.task_type,
            selected_model_id=e.selected_model_id,
            cost_usd=e.cost_usd,
            latency_ms=e.latency_ms,
            llm_calls=e.llm_calls,
            confidence=e.confidence,
            preview=preview_of(e),
        )
        for e in result.scalars()
    ]


@router.get("/executions/{execution_id}")
async def get_execution(execution_id: str, session: SessionDep, scope: ScopeDep) -> dict:
    execution = await get_execution_scoped(session, execution_id, scope)
    if execution is None:
        raise NotFoundError("Execution not found.")
    evaluation = (
        await session.execute(select(Evaluation).where(Evaluation.execution_id == execution.id))
    ).scalar_one_or_none()

    return {
        "id": execution.id,
        "created_at": execution.created_at.isoformat(),
        "status": execution.status,
        "mode": execution.mode,
        "mode_reason": execution.mode_reason,
        "task_type": execution.task_type,
        "complexity": execution.complexity,
        "risk_level": execution.risk_level,
        "confidence": execution.confidence,
        "generated_by": "ai",
        "input": execution.raw_input,
        "output": execution.output,
        "content_retained": not execution.input_redacted,
        "selected_model_id": execution.selected_model_id,
        "routing_strategy": execution.routing_strategy,
        "llm_calls": execution.llm_calls,
        "cost_usd": execution.cost_usd,
        "orchestration_cost_usd": execution.orchestration_cost_usd,
        "latency_ms": execution.latency_ms,
        "plan": execution.plan,
        "trace": execution.trace,
        "warnings": execution.warnings,
        "error": execution.error,
        "evaluation": (
            {
                "status": evaluation.status,
                "selection_reason": evaluation.selection_reason,
                "overall_score": evaluation.overall_score,
                "model_agreement": evaluation.model_agreement,
                "mitigations": evaluation.mitigations,
                "results": evaluation.results,
            }
            if evaluation
            else None
        ),
    }


@router.post("/feedback", status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    body: schemas.FeedbackRequest, session: SessionDep, scope: ScopeDep
) -> dict[str, str]:
    """Human signal — the scarcest and most valuable evaluation input (rule 23).

    Stored, never acted on automatically: a single interaction must not change production behaviour.
    It becomes dataset material for controlled experiments instead.
    """
    execution = await get_execution_scoped(session, body.execution_id, scope)
    if execution is None:
        raise NotFoundError("Execution not found.")

    session.add(
        Feedback(
            org_id=scope.org_id,
            execution_id=execution.id,
            user_id=scope.user_id,
            rating=body.rating,
            reason=body.reason,
            comment=body.comment,
            edited_output=body.edited_output,
        )
    )
    await record_audit(
        session,
        action="feedback.submitted",
        org_id=scope.org_id,
        user_id=scope.user_id,
        resource_type="execution",
        resource_id=execution.id,
        detail={"rating": body.rating, "reason": body.reason or ""},
    )
    return {
        "status": "recorded",
        "note": (
            "Feedback is recorded for measurement. It does not change production behaviour on its own; "
            "changes are made through controlled experiments."
        ),
    }


@router.get("/analytics", response_model=schemas.AnalyticsResponse)
async def analytics(
    session: SessionDep,
    scope: ScopeDep,
    org: OrgDep,
    days: int = Query(default=30, ge=1, le=365),
) -> schemas.AnalyticsResponse:
    since = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        select(Execution).where(Execution.org_id == scope.org_id, Execution.created_at >= since)
    )
    executions = list(result.scalars())

    total_cost = sum(e.cost_usd for e in executions)
    orchestration_cost = sum(e.orchestration_cost_usd for e in executions)

    positive = (
        await session.execute(
            select(func.count())
            .select_from(Feedback)
            .where(
                Feedback.org_id == scope.org_id,
                Feedback.rating == "up",
                Feedback.created_at >= since,
            )
        )
    ).scalar_one()
    negative = (
        await session.execute(
            select(func.count())
            .select_from(Feedback)
            .where(
                Feedback.org_id == scope.org_id,
                Feedback.rating == "down",
                Feedback.created_at >= since,
            )
        )
    ).scalar_one()

    n = len(executions) or 1
    return schemas.AnalyticsResponse(
        period_days=days,
        executions=len(executions),
        total_cost_usd=round(total_cost, 6),
        generation_cost_usd=round(total_cost - orchestration_cost, 6),
        orchestration_cost_usd=round(orchestration_cost, 6),
        orchestration_ratio=round(orchestration_cost / total_cost, 3) if total_cost > 0 else 0.0,
        avg_latency_ms=int(sum(e.latency_ms for e in executions) / n),
        avg_llm_calls=round(sum(e.llm_calls for e in executions) / n, 2),
        mode_distribution=dict(Counter(e.mode for e in executions)),
        task_distribution=dict(Counter(e.task_type for e in executions)),
        model_distribution=dict(Counter(e.selected_model_id for e in executions if e.selected_model_id)),
        feedback_positive=positive,
        feedback_negative=negative,
        budget_limit_usd=org.monthly_budget_usd,
        budget_spent_usd=round(org.current_spend_usd, 6),
    )
