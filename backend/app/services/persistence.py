"""Persisting executions, usage and audit events.

Privacy rule (rule 28 / rule 50): raw input and output are stored **only** when the organization has
explicitly opted in via `store_raw_prompts`. Otherwise we keep the metadata that makes the product work —
decisions, costs, evaluation results — and none of the user's content.

Budget accounting is denormalised onto the organization row because it is read on every request for the
pre-flight check; a SUM over the ledger per request would be a hot aggregate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TenantScope
from app.db.models import (
    AuditLog,
    Evaluation,
    Execution,
    ModelCall,
    Organization,
    UsageRecordRow,
)
from app.pipeline.orchestrator import ExecutionResult

PREVIEW_CHARS = 160


async def save_execution(
    session: AsyncSession,
    result: ExecutionResult,
    scope: TenantScope,
    *,
    store_raw: bool,
    project_id: str | None = None,
) -> Execution:
    spec = result.spec
    usage = result.cost.total_usage
    selected = result.selected_candidate

    row = Execution(
        id=result.execution_id,
        org_id=scope.org_id,
        user_id=scope.user_id,
        project_id=project_id,
        status=result.status,
        mode=result.mode.value,
        mode_reason=result.mode_reason,
        raw_input=(spec.raw_input if store_raw and spec else None),
        output=(result.output if store_raw else None),
        input_redacted=not store_raw,
        task_type=spec.task_type.value if spec else "general_qa",
        complexity=spec.complexity.value if spec else "medium",
        risk_level=spec.risk_level.value if spec else "low",
        confidence=result.confidence.value,
        routing_strategy=result.plan.strategy.value if result.plan else "single",
        selected_model_id=selected.model_id if selected else "",
        llm_calls=result.trace.llm_call_count() if result.trace else 0,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=result.cost.total_usd,
        orchestration_cost_usd=result.cost.orchestration_usd,
        latency_ms=result.trace.total_duration_ms if result.trace else 0,
        spec=_spec_snapshot(result, store_raw=store_raw),
        plan=result.plan.model_dump() if result.plan else {},
        trace=result.trace.to_dict() if result.trace else {},
        warnings=result.warnings,
        error=result.error,
    )
    session.add(row)
    # Flush immediately: ModelCall/UsageRecordRow/Evaluation below reference `row.id` as a foreign key,
    # but no ORM `relationship()` connects these models (deliberately — they are independent append-only
    # records, not a navigable object graph). Without a relationship, SQLAlchemy's flush has no way to
    # infer that Execution must be inserted first, and can order the INSERTs arbitrarily, which trips the
    # FK constraint and discards the entire execution record. This was caught by hitting the real
    # Postgres backend directly (not the SQLite test suite, which is not FK-constrained by default) and
    # is exactly the kind of bug a schema-only test suite cannot surface.
    await session.flush()

    for record in result.cost.records:
        session.add(
            ModelCall(
                org_id=scope.org_id,
                execution_id=row.id,
                model_id=record.model_id,
                provider=record.provider,
                purpose=record.purpose,
                input_tokens=record.usage.input_tokens,
                output_tokens=record.usage.output_tokens,
                cache_read_tokens=record.usage.cache_read_tokens,
                cost_usd=record.cost_usd,
                latency_ms=record.latency_ms,
                from_cache=record.from_cache,
                pricing_verified=record.verified_pricing,
            )
        )
        # The ledger is separate and content-free, so it survives retention deletion of the execution.
        session.add(
            UsageRecordRow(
                org_id=scope.org_id,
                execution_id=row.id,
                model_id=record.model_id,
                provider=record.provider,
                purpose=record.purpose,
                input_tokens=record.usage.input_tokens,
                output_tokens=record.usage.output_tokens,
                cost_usd=record.cost_usd,
            )
        )

    if result.evaluation is not None:
        report = result.evaluation
        selected_eval = (
            report.for_candidate(report.selected_candidate_id)
            if report.selected_candidate_id
            else None
        )
        session.add(
            Evaluation(
                org_id=scope.org_id,
                execution_id=row.id,
                status=report.status.value,
                selected_candidate_id=report.selected_candidate_id,
                selection_reason=report.selection_reason,
                overall_score=selected_eval.overall if selected_eval else None,
                model_agreement=report.model_agreement,
                mitigations=report.mitigations.model_dump() if report.mitigations else {},
                results={
                    "evaluations": [e.model_dump() for e in report.evaluations],
                },
                cost_usd=report.cost_usd,
            )
        )

    await _accrue_spend(session, scope.org_id, result.cost.total_usd)
    return row


def _spec_snapshot(result: ExecutionResult, *, store_raw: bool) -> dict[str, Any]:
    """Spec without user content unless raw storage is enabled."""
    if result.spec is None:
        return {}
    data = result.spec.model_dump(mode="json")
    if not store_raw:
        for field in ("raw_input", "goal", "context", "entities"):
            data.pop(field, None)
    return data


async def _accrue_spend(session: AsyncSession, org_id: str, amount_usd: float) -> None:
    org = await session.get(Organization, org_id)
    if org is None:
        return
    now = datetime.now(UTC)
    period_start = org.spend_period_start
    if period_start.tzinfo is None:
        period_start = period_start.replace(tzinfo=UTC)
    # Rolling 30-day window rather than calendar month: simpler, and matches how the budget is described
    # to the user ("per month" from signup).
    if now - period_start > timedelta(days=30):
        org.current_spend_usd = 0.0
        org.spend_period_start = now
    org.current_spend_usd = round(org.current_spend_usd + amount_usd, 8)


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    org_id: str | None = None,
    user_id: str | None = None,
    resource_type: str = "",
    resource_id: str = "",
    ip_address: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            org_id=org_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            detail=detail or {},
        )
    )


async def get_execution_scoped(
    session: AsyncSession, execution_id: str, scope: TenantScope
) -> Execution | None:
    """Always filter by org_id.

    Cross-tenant access returns None so the caller raises 404 rather than 403 — a 403 confirms the
    resource exists, which is itself a leak (ARCHITECTURE §7).
    """
    result = await session.execute(
        select(Execution).where(Execution.id == execution_id, Execution.org_id == scope.org_id)
    )
    return result.scalar_one_or_none()


def preview_of(execution: Execution) -> str:
    if execution.raw_input:
        return execution.raw_input[:PREVIEW_CHARS]
    return f"[{execution.task_type} · content not retained]"
