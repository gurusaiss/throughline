"""Retention job tests.

Run against real Postgres (not SQLite) because the whole point of this job is a database-level `ON DELETE
CASCADE`, and that behaviour must be verified against the engine that will actually run it in production.
Skips cleanly when no Postgres is reachable rather than failing CI environments that don't provision one
for this specific suite.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import (
    AuditLog,
    Base,
    Evaluation,
    Execution,
    ModelCall,
    Organization,
    UsageRecordRow,
)
from app.services.retention import purge_expired

POSTGRES_URL = "postgresql+asyncpg://throughline:throughline@localhost:5432/throughline_retention_test"


@pytest.fixture
async def pg_session():
    import asyncpg

    try:
        admin_conn = await asyncpg.connect(
            "postgresql://throughline:throughline@localhost:5432/throughline"
        )
    except Exception:
        pytest.skip("Postgres is not reachable in this environment")
        return

    db_name = "throughline_retention_test"
    await admin_conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
    await admin_conn.execute(f'CREATE DATABASE "{db_name}"')
    await admin_conn.close()

    engine = create_async_engine(POSTGRES_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        yield session

    await engine.dispose()
    admin_conn = await asyncpg.connect(
        "postgresql://throughline:throughline@localhost:5432/throughline"
    )
    await admin_conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
    await admin_conn.close()


async def _make_org(session, *, retention_days: int = 90) -> Organization:
    org = Organization(
        name="Retention Org", slug=f"retention-org-{secrets.token_hex(8)}", retention_days=retention_days
    )
    session.add(org)
    await session.flush()
    return org


async def _make_execution(session, org_id: str, *, age_days: int) -> Execution:
    execution = Execution(
        org_id=org_id,
        status="ok",
        created_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    session.add(execution)
    await session.flush()
    return execution


class TestRetentionPurge:
    async def test_dry_run_counts_without_deleting(self, pg_session) -> None:  # noqa: ANN001
        org = await _make_org(pg_session, retention_days=30)
        await _make_execution(pg_session, org.id, age_days=60)  # expired
        await _make_execution(pg_session, org.id, age_days=5)  # not expired
        await pg_session.commit()

        report = await purge_expired(pg_session, dry_run=True)

        assert report.total_deleted == 1
        remaining = (
            await pg_session.execute(select(Execution).where(Execution.org_id == org.id))
        ).scalars().all()
        assert len(remaining) == 2  # nothing was actually deleted

    async def test_real_run_deletes_only_expired_executions(self, pg_session) -> None:  # noqa: ANN001
        org = await _make_org(pg_session, retention_days=30)
        expired = await _make_execution(pg_session, org.id, age_days=60)
        kept = await _make_execution(pg_session, org.id, age_days=5)
        await pg_session.commit()

        report = await purge_expired(pg_session, dry_run=False)

        assert report.total_deleted == 1
        remaining_ids = {
            e.id
            for e in (
                await pg_session.execute(select(Execution).where(Execution.org_id == org.id))
            ).scalars()
        }
        assert remaining_ids == {kept.id}
        assert expired.id not in remaining_ids

    async def test_cascade_deletes_model_calls_and_evaluations(self, pg_session) -> None:  # noqa: ANN001
        """The whole design hinges on the DB-level ON DELETE CASCADE actually firing (docs/DATABASE.md)."""
        org = await _make_org(pg_session, retention_days=1)
        execution = await _make_execution(pg_session, org.id, age_days=30)
        pg_session.add(
            ModelCall(org_id=org.id, execution_id=execution.id, model_id="m", provider="p")
        )
        pg_session.add(
            Evaluation(org_id=org.id, execution_id=execution.id, status="complete")
        )
        await pg_session.commit()

        await purge_expired(pg_session, dry_run=False)

        remaining_calls = (
            await pg_session.execute(select(ModelCall).where(ModelCall.execution_id == execution.id))
        ).scalars().all()
        remaining_evals = (
            await pg_session.execute(select(Evaluation).where(Evaluation.execution_id == execution.id))
        ).scalars().all()
        assert remaining_calls == []
        assert remaining_evals == []

    async def test_never_touches_the_billing_ledger_or_audit_log(self, pg_session) -> None:  # noqa: ANN001
        """UsageRecordRow and AuditLog must survive execution deletion — that asymmetry is the entire
        point of the design (billing integrity and incident investigation outlive content retention)."""
        org = await _make_org(pg_session, retention_days=1)
        execution = await _make_execution(pg_session, org.id, age_days=30)
        pg_session.add(
            UsageRecordRow(
                org_id=org.id, execution_id=execution.id, model_id="m", provider="p", purpose="generate"
            )
        )
        pg_session.add(AuditLog(org_id=org.id, action="test.event", resource_id=execution.id))
        await pg_session.commit()

        await purge_expired(pg_session, dry_run=False)

        usage_rows = (
            await pg_session.execute(select(UsageRecordRow).where(UsageRecordRow.org_id == org.id))
        ).scalars().all()
        audit_rows = (
            await pg_session.execute(select(AuditLog).where(AuditLog.org_id == org.id))
        ).scalars().all()
        assert len(usage_rows) == 1
        assert len(audit_rows) == 1

    async def test_each_org_uses_its_own_retention_window(self, pg_session) -> None:  # noqa: ANN001
        strict_org = await _make_org(pg_session, retention_days=7)
        lenient_org = await _make_org(pg_session, retention_days=365)
        await _make_execution(pg_session, strict_org.id, age_days=30)  # expired for strict
        await _make_execution(pg_session, lenient_org.id, age_days=30)  # not expired for lenient
        await pg_session.commit()

        report = await purge_expired(pg_session, dry_run=False)

        assert report.total_deleted == 1
        assert {r.org_id for r in report.results} == {strict_org.id}

    async def test_batches_large_deletions_without_one_giant_transaction(self, pg_session) -> None:  # noqa: ANN001
        """BATCH_SIZE=1000 in production; this proves the batching loop terminates correctly with a
        smaller override so the test runs fast while still exercising more than one batch."""
        import app.services.retention as retention_module

        original_batch_size = retention_module.BATCH_SIZE
        retention_module.BATCH_SIZE = 3
        try:
            org = await _make_org(pg_session, retention_days=1)
            for _ in range(10):
                await _make_execution(pg_session, org.id, age_days=30)
            await pg_session.commit()

            report = await purge_expired(pg_session, dry_run=False)
            assert report.total_deleted == 10

            remaining = (
                await pg_session.execute(select(Execution).where(Execution.org_id == org.id))
            ).scalars().all()
            assert remaining == []
        finally:
            retention_module.BATCH_SIZE = original_batch_size

    async def test_org_with_nothing_expired_is_not_in_the_report(self, pg_session) -> None:  # noqa: ANN001
        org = await _make_org(pg_session, retention_days=90)
        await _make_execution(pg_session, org.id, age_days=1)
        await pg_session.commit()

        report = await purge_expired(pg_session, dry_run=False)
        assert org.id not in {r.org_id for r in report.results}
