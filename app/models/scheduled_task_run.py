import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    Enum,
    Index,
    Numeric,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models.enums import JobId, enum_values


class ScheduledTaskRun(Base):
    """One row per completed scheduled-job invocation (app.core.scheduler).

    Stage-local by design: excluded from pg_dump via --exclude-table-data
    (see app/services/backup_service.py::run_backup()) so a prod->dev
    restore/downsync never mixes another stage's run history into this
    one — each stage's scheduler is configured independently and only its
    own runs are meaningful here.

    UUID primary key for schema-wide consistency, not for enumeration-
    resistance: unlike public_gallery_images, id is never exposed on a
    public/unauthenticated endpoint (systemAdmin-only) and nothing else in
    the schema has a FK to it, so that particular argument doesn't apply
    here - it was outweighed by the project-wide decision to use UUIDs
    across every table without exception (see
    22fc473b0891_sent_emails_and_scheduled_task_runs_ids_.py, which
    revisits 9f8ff3c10cb2's earlier choice to move this table back onto an
    integer id for exactly the same lack of an enumeration-risk argument).
    """

    __tablename__ = "scheduled_task_runs"
    __table_args__ = (
        CheckConstraint("exit_code >= 0", name="scheduled_task_runs_exit_code_check"),
        CheckConstraint(
            "finished_at >= started_at",
            name="scheduled_task_runs_started_finished_check",
        ),
        Index(
            "ix_scheduled_task_runs_job_id_started_at",
            "job_id",
            "started_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    job_id: Mapped[JobId] = mapped_column(
        Enum(JobId, name="job_id", native_enum=True, values_callable=enum_values)
    )
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
