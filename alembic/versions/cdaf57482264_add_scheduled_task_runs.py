"""add scheduled task runs

Revision ID: cdaf57482264
Revises: 4424710ac2b0
Create Date: 2026-08-04 12:03:03.111360

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cdaf57482264"
down_revision: str | Sequence[str] | None = "4424710ac2b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept in sync with app.models.scheduled_task_run.KNOWN_JOB_IDS — extending
# the job registry in app/core/scheduler.py requires a follow-up migration
# to add the new id here too.
_KNOWN_JOB_IDS = (
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


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    job_id_list = ", ".join(f"'{job_id}'" for job_id in _KNOWN_JOB_IDS)

    op.create_table(
        "scheduled_task_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "duration_seconds",
            sa.Numeric(),
            sa.Computed(
                "EXTRACT(EPOCH FROM (finished_at - started_at))",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column("exit_code", sa.Integer(), nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            f"job_id IN ({job_id_list})",
            name="scheduled_task_runs_job_id_check",
        ),
        sa.CheckConstraint(
            "exit_code >= 0", name="scheduled_task_runs_exit_code_check"
        ),
    )
    op.create_index(
        "ix_scheduled_task_runs_job_id_started_at",
        "scheduled_task_runs",
        ["job_id", "started_at"],
    )
    op.execute(
        "CREATE TRIGGER scheduled_task_runs_set_updated_at "
        "BEFORE UPDATE ON scheduled_task_runs "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "DROP TRIGGER IF EXISTS scheduled_task_runs_set_updated_at "
        "ON scheduled_task_runs"
    )
    op.drop_index(
        "ix_scheduled_task_runs_job_id_started_at",
        table_name="scheduled_task_runs",
    )
    op.drop_table("scheduled_task_runs")
