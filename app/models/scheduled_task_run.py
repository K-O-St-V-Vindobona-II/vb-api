import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    Index,
    Numeric,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# Every id currently registered in app.core.scheduler's job registry. Kept
# here (not imported from scheduler.py, to avoid a models -> core import)
# purely for documentation — the actual enforcement is the CHECK constraint
# below, which must be extended in a migration whenever a new job is added.
KNOWN_JOB_IDS = (
    "cleanup",
    "refresh_category_filter_hits",
    "birthday_mails",
    "debtor_reminder",
    "standesdb_chronicles",
    "archive_health_check",
    "standesdb_health_check",
    "db_backup",
    "downsync",
)


class ScheduledTaskRun(Base):
    """One row per completed scheduled-job invocation (app.core.scheduler).

    Stage-local by design: excluded from pg_dump via --exclude-table-data
    (see app/services/backup_service.py::run_backup()) so a prod->dev
    restore/downsync never mixes another stage's run history into this
    one — each stage's scheduler is configured independently and only its
    own runs are meaningful here.
    """

    __tablename__ = "scheduled_task_runs"
    __table_args__ = (
        CheckConstraint(
            "job_id IN (" + ", ".join(f"'{job_id}'" for job_id in KNOWN_JOB_IDS) + ")",
            name="scheduled_task_runs_job_id_check",
        ),
        CheckConstraint("exit_code >= 0", name="scheduled_task_runs_exit_code_check"),
        Index(
            "ix_scheduled_task_runs_job_id_started_at",
            "job_id",
            "started_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[Decimal] = mapped_column(
        Numeric,
        Computed(
            "EXTRACT(EPOCH FROM (finished_at - started_at))",
            persisted=True,
        ),
    )
    exit_code: Mapped[int]
    output: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
