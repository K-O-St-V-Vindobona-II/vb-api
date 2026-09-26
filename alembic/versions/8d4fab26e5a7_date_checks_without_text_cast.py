"""compare dates as dates in two CHECK constraints

Revision ID: 8d4fab26e5a7
Revises: 7c3e9a15d4f6
Create Date: 2026-09-21 09:40:00.000000

members_roles_startdate_enddate_check and
p4x_summary_orders_summary_start_end_check were created while their columns
were text (cb250c054945). When the columns became DATE, PostgreSQL kept the
implicit cast and the stored expressions read `startdate::text < enddate::text`.
That comparison depends on the session's DateStyle: under `German, DMY` a
valid interval is rejected and an invalid one is accepted. The constraints are
recreated with the expressions the models declare. Existing rows satisfied the
ISO text form, which is equivalent to the date comparison, so they satisfy the
new one.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8d4fab26e5a7"
down_revision: str | Sequence[str] | None = "7c3e9a15d4f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, constraint, date expression, former text expression)
_CONSTRAINTS: list[tuple[str, str, str, str]] = [
    (
        "members_roles",
        "members_roles_startdate_enddate_check",
        "enddate IS NULL OR startdate < enddate",
        "enddate IS NULL OR startdate::text < enddate::text",
    ),
    (
        "p4x_summary_orders",
        "p4x_summary_orders_summary_start_end_check",
        "summary_end >= summary_start",
        "summary_end::text >= summary_start::text",
    ),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table, name, expression, _ in _CONSTRAINTS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, expression)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table, name, _, text_expression in _CONSTRAINTS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, text_expression)
