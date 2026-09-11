"""scheduled task runs started finished check

Revision ID: d2d135fcfb3f
Revises: 3c528b0f621f
Create Date: 2026-09-11 23:44:45.474005

duration_seconds is a GENERATED column computed from
finished_at - started_at, but nothing enforced finished_at >= started_at
itself — unlike the analogous ordering rules already in place elsewhere
(p4x_summary_orders_summary_start_end_check,
members_roles_startdate_enddate_check). Without it, a malformed job-run
report could silently produce a negative duration.

No data risk: verified against the live table before writing this
migration (0 rows violate the rule out of 227).
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2d135fcfb3f"
down_revision: str | None = "3c528b0f621f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint(
        "scheduled_task_runs_started_finished_check",
        "scheduled_task_runs",
        "finished_at >= started_at",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "scheduled_task_runs_started_finished_check",
        "scheduled_task_runs",
        type_="check",
    )
