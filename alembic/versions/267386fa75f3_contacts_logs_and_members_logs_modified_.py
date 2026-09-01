"""contacts_logs and members_logs modified_by fk

Revision ID: 267386fa75f3
Revises: c963591a4c7a
Create Date: 2026-09-01 22:40:00.000000

contacts_logs.modified_by and members_logs.modified_by were bare
integer columns with no ForeignKey() at all - not a Referrer-Cutover of
an existing FK, but adding a genuinely missing one. Both now reference
members.id_uuid, not members.id - members itself won't have a UUID
primary key until its own Final-Cutover.

Data cleanup first: both columns hold 0 as a legacy "no member
attributed" sentinel from the predecessor system (680/700 contacts_logs
rows, 13446/15750 members_logs rows) - a value that never matches any
real members.id, which the read side already treats as falsy/absent
(see standesdb_service.py's `if e.modified_by`). Converted to NULL
before adding the FK, since that already is the column's actual
nullable-and-optional meaning; every other value is a real member id.
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "267386fa75f3"
down_revision: str | Sequence[str] | None = "c963591a4c7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, fk constraint name)
LOG_TABLES = (
    ("contacts_logs", "contacts_logs_modified_by_fkey"),
    ("members_logs", "members_logs_modified_by_fkey"),
)


def _cutover_modified_by(table: str, fk_name: str) -> None:
    op.execute(f"UPDATE {table} SET modified_by = NULL WHERE modified_by = 0")  # noqa: S608
    op.add_column(table, sa.Column("modified_by_uuid", sa.Uuid(), nullable=True))
    op.execute(
        f"UPDATE {table} t SET modified_by_uuid = m.id_uuid "  # noqa: S608
        f"FROM members m WHERE t.modified_by = m.id"
    )
    op.drop_column(table, "modified_by")
    op.alter_column(table, "modified_by_uuid", new_column_name="modified_by")
    op.create_foreign_key(
        fk_name,
        table,
        "members",
        ["modified_by"],
        ["id_uuid"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def _revert_modified_by(table: str, fk_name: str) -> None:
    op.add_column(table, sa.Column("modified_by_int", sa.Integer(), nullable=True))
    op.execute(
        f"UPDATE {table} t SET modified_by_int = m.id "  # noqa: S608
        f"FROM members m WHERE t.modified_by = m.id_uuid"
    )
    op.drop_constraint(fk_name, table, type_="foreignkey")
    op.drop_column(table, "modified_by")
    op.alter_column(table, "modified_by_int", new_column_name="modified_by")


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table, fk_name in LOG_TABLES:
        _cutover_modified_by(table, fk_name)


def downgrade() -> None:
    """Downgrade schema.

    Not loss-free for the rows that were 0 before upgrade() - those are
    NULL after downgrade() too, since the original sentinel value can't
    be distinguished from a genuine NULL any more. Every other row is
    loss-free (round-trips through members.id/id_uuid unchanged).
    """
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table, fk_name in reversed(LOG_TABLES):
        _revert_modified_by(table, fk_name)
