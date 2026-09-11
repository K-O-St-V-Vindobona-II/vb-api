"""job id native enum

Revision ID: 3c528b0f621f
Revises: a1fbacf33990
Create Date: 2026-09-11 23:44:44.627426

scheduled_task_runs.job_id was the schema's one remaining bounded-value
column still enforced via TEXT + a hand-written CHECK (job_id IN (...))
instead of a native Postgres ENUM, unlike every other closed-vocabulary
column (contact_type, badge_group, role_group, p4x_filter_subject_mode,
member_delivery_preference, changelog_action,
member_change_request_status, about_tab_slot — all converted in
6f7ce9023e0e/42df0f369b0b). The nine job ids were also independently
duplicated as raw string literals in three places (the CHECK itself, the
cron schedule registry, and the ARQ dispatch table) instead of binding to
one source of truth, unlike those other columns' Python StrEnum classes.

Both fixed together here: app.models.enums.JobId is now that single
source, and this migration replaces the CHECK with the matching native
ENUM type. No data risk — the existing CHECK already restricts the
column to exactly these nine values.
"""

from typing import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3c528b0f621f"
down_revision: str | None = "a1fbacf33990"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

job_id_enum = postgresql.ENUM(
    "cleanup",
    "refresh_category_filter_hits",
    "birthday_mails",
    "debtor_reminder",
    "standesdb_chronicles",
    "archive_health_check",
    "standesdb_health_check",
    "db_backup",
    "downsync",
    name="job_id",
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.drop_constraint(
        "scheduled_task_runs_job_id_check", "scheduled_task_runs", type_="check"
    )

    job_id_enum.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "scheduled_task_runs",
        "job_id",
        existing_type=sa.Text(),
        type_=job_id_enum,
        existing_nullable=False,
        postgresql_using="job_id::job_id",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.alter_column(
        "scheduled_task_runs",
        "job_id",
        existing_type=job_id_enum,
        type_=sa.Text(),
        existing_nullable=False,
        postgresql_using="job_id::text",
    )
    job_id_enum.drop(op.get_bind(), checkfirst=True)

    op.create_check_constraint(
        "scheduled_task_runs_job_id_check",
        "scheduled_task_runs",
        "job_id IN ('cleanup', 'refresh_category_filter_hits', 'birthday_mails', "
        "'debtor_reminder', 'standesdb_chronicles', 'archive_health_check', "
        "'standesdb_health_check', 'db_backup', 'downsync')",
    )
