"""scheduled_task_runs id to autoincrement

Revision ID: 9f8ff3c10cb2
Revises: cdaf57482264
Create Date: 2026-08-04 13:21:37.129461

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f8ff3c10cb2"
down_revision: str | Sequence[str] | None = "cdaf57482264"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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


def _create_table(id_column: sa.Column) -> None:
    job_id_list = ", ".join(f"'{job_id}'" for job_id in _KNOWN_JOB_IDS)

    op.create_table(
        "scheduled_task_runs",
        id_column,
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


def upgrade() -> None:
    """Upgrade schema.

    UUID was a reflexive "new table -> UUID" choice, not a reasoned one —
    unlike public_gallery_images, this table's id is never exposed on a
    public/unauthenticated endpoint (systemAdmin-only) and nothing else in
    the schema has (or ever will have) a FK to it, so there's no
    enumeration-risk argument for keeping it. Drop+recreate rather than an
    in-place ALTER: the table holds zero rows worth preserving at this
    point (brand new, this environment's own test rows already cleaned
    up), so a type-converting ALTER would be unnecessary complexity for no
    benefit.

    Note: 22fc473b0891_sent_emails_and_scheduled_task_runs_ids_.py later
    moves this table back onto a UUID primary key as part of a schema-wide
    consistency decision that applies to every table regardless of its
    individual enumeration-risk profile — the reasoning above no longer
    drives the choice, it's superseded outright.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "DROP TRIGGER IF EXISTS scheduled_task_runs_set_updated_at"
        " ON scheduled_task_runs"
    )
    op.drop_table("scheduled_task_runs")
    _create_table(sa.Column("id", sa.Integer(), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "DROP TRIGGER IF EXISTS scheduled_task_runs_set_updated_at"
        " ON scheduled_task_runs"
    )
    op.drop_table("scheduled_task_runs")
    _create_table(sa.Column("id", sa.Uuid(), nullable=False))
