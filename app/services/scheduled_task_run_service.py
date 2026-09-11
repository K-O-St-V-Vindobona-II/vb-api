"""Persistence and querying for scheduled-job run history.

record_job_run() is called explicitly by each job_*() function in
app.core.scheduler at its success and failure exit points — see that
module's docstring for why this is not automatic log-capture. It always
opens its own fresh session and never raises: recording a run must never
be the reason a scheduled job's own error handling breaks.
"""

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypedDict

from app.db.database import SessionLocal
from app.models.scheduled_task_run import ScheduledTaskRun

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.enums import JobId

logger = logging.getLogger(__name__)

_OUTPUT_MAX_LENGTH = 4000


class PaginatedRuns(TypedDict):
    items: list[ScheduledTaskRun]
    total: int
    page: int
    page_size: int


def record_job_run(
    job_id: JobId,
    started_at: datetime,
    *,
    exit_code: int,
    output: str | None,
) -> None:
    try:
        db = SessionLocal()
        try:
            db.add(
                ScheduledTaskRun(
                    job_id=job_id,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    exit_code=exit_code,
                    output=output[:_OUTPUT_MAX_LENGTH] if output else output,
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        # CodeQL flags job_id here as clear-text logging of sensitive data
        # purely because of the JobId.BIRTHDAY_MAILS member name - it
        # traces the enum member's identifier, not its actual value or any
        # real date-of-birth data. job_id is always one of nine internal
        # job identifiers (e.g. "cleanup", "birthday_mails"), never a
        # value derived from member PII. False positive.
        logger.exception(  # lgtm[py/clear-text-logging-sensitive-data]
            "Failed to record run history for job %s", job_id
        )


def list_job_runs(db: Session, job_id: str, page: int, page_size: int) -> PaginatedRuns:
    """job_id stays a plain str here (unlike record_job_run's JobId): this
    is fed straight from the systemAdmin API's path parameter, an
    unvalidated client-supplied value — an unknown id simply matches no
    rows rather than needing router-level enum coercion."""
    query = db.query(ScheduledTaskRun).filter(ScheduledTaskRun.job_id == job_id)
    total = query.count()
    items = (
        query.order_by(ScheduledTaskRun.started_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_latest_run_per_job(db: Session) -> dict[str, ScheduledTaskRun]:
    """One query for every job's most recent run, keyed by job_id — avoids
    N+1 when merged into the scheduled-jobs list (one job registry, one
    query, not one query per job). Keyed by str, not JobId: the caller
    looks this up against get_scheduled_jobs()'s plain str ids without
    needing its own JobId conversion."""
    rows = (
        db.query(ScheduledTaskRun)
        .distinct(ScheduledTaskRun.job_id)
        .order_by(ScheduledTaskRun.job_id, ScheduledTaskRun.started_at.desc())
        .all()
    )
    return {str(row.job_id): row for row in rows}
