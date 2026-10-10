"""Data retention: deletes content past each organization's own `retention_days`.

The asymmetry is the whole design (see docs/DATABASE.md): `Execution` rows — and, by database-level
`ON DELETE CASCADE` foreign keys, their `ModelCall`, `Evaluation` and `Feedback` children — are deleted
once past the retention window. `UsageRecordRow` (the billing ledger) and `AuditLog` (the security record)
are NEVER touched here: neither holds user content, so neither needs to be deleted for privacy, and both
must survive for billing integrity and incident investigation respectively. This file does not delete from
those tables, and must never be changed to do so without re-reading that design decision.

Run as a scheduled job (cron, a Kubernetes CronJob, etc.) — this process does not schedule itself:

    python -m app.services.retention             # deletes
    python -m app.services.retention --dry-run    # reports what WOULD be deleted, deletes nothing
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Execution, Organization
from app.db.session import dispose_engine, get_sessionmaker, init_engine

logger = logging.getLogger("throughline.retention")

# Deleting in bounded batches rather than one giant DELETE: a tenant with millions of expired executions
# would otherwise hold one enormous transaction (locking the table and risking a timeout/OOM) instead of
# making steady, interruptible progress.
BATCH_SIZE = 1000


@dataclass
class OrgPurgeResult:
    org_id: str
    org_name: str
    retention_days: int
    cutoff: str
    deleted: int = 0


@dataclass
class PurgeReport:
    dry_run: bool
    results: list[OrgPurgeResult] = field(default_factory=list)

    @property
    def total_deleted(self) -> int:
        return sum(r.deleted for r in self.results)


async def purge_expired(
    session: AsyncSession, *, now: datetime | None = None, dry_run: bool = False
) -> PurgeReport:
    """Delete (or, with dry_run, count) executions past each org's own retention window."""
    now = now or datetime.now(UTC)
    report = PurgeReport(dry_run=dry_run)

    orgs = (await session.execute(select(Organization))).scalars().all()
    for org in orgs:
        cutoff = now - timedelta(days=org.retention_days)
        result = OrgPurgeResult(
            org_id=org.id, org_name=org.name, retention_days=org.retention_days, cutoff=cutoff.isoformat()
        )

        if dry_run:
            # A real COUNT, not a sampled batch — an operator deciding whether to run this for real
            # needs the true blast radius, and reporting only the first BATCH_SIZE rows would silently
            # under-report any org with more than that many expired executions.
            result.deleted = (
                await session.execute(
                    select(func.count())
                    .select_from(Execution)
                    .where(Execution.org_id == org.id, Execution.created_at < cutoff)
                )
            ).scalar_one()
        else:
            while True:
                ids = (
                    await session.execute(
                        select(Execution.id)
                        .where(Execution.org_id == org.id, Execution.created_at < cutoff)
                        .limit(BATCH_SIZE)
                    )
                ).scalars().all()
                if not ids:
                    break

                await session.execute(delete(Execution).where(Execution.id.in_(ids)))
                await session.commit()
                result.deleted += len(ids)

                if len(ids) < BATCH_SIZE:
                    break  # last batch was partial: nothing left for this org

        if result.deleted:
            report.results.append(result)
            logger.info(
                "org=%s (%s): %s %d execution(s) older than %s",
                org.id, org.name, "would delete" if dry_run else "deleted", result.deleted, result.cutoff,
            )

    return report


async def _run(dry_run: bool) -> PurgeReport:
    settings = get_settings()
    init_engine(settings)
    try:
        async with get_sessionmaker()() as session:
            return await purge_expired(session, dry_run=dry_run)
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be deleted without deleting anything.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report = asyncio.run(_run(args.dry_run))

    verb = "Would delete" if report.dry_run else "Deleted"
    logger.info("%s %d execution(s) across %d organization(s).", verb, report.total_deleted, len(report.results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
