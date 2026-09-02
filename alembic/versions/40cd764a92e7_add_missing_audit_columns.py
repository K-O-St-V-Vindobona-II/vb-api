"""add missing audit columns

Revision ID: 40cd764a92e7
Revises: fdd134a00dd3
Create Date: 2026-09-02 23:20:00.000000

8 tables never got created_at/updated_at, the one audit-trail catalog
point CLAUDE.md requires unconditionally outside genuine mapping tables
(74d19e4af679 wired up set_updated_at() for 17 tables back in Welle 2
Slice 3; these 8 were never added to that list). None are junction
tables (badges_members/keys_members/members_roles - the actual mapping
tables with no surrogate id - correctly stay exempt), so there's no
structural reason for the gap; found via a direct information_schema
sweep, independent of anything the UUID-PK migration touched.

Both columns get DEFAULT now() so existing rows aren't left NULL - this
means every pre-existing row's created_at/updated_at collapses to the
migration's run time (no real historical data to backfill from), which
is an accepted, unavoidable limitation of retrofitting audit columns
onto an already-populated table.
"""

from typing import Sequence

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "40cd764a92e7"
down_revision: str | None = "fdd134a00dd3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [
    "orgs",
    "roles",
    "states",
    "badges",
    "keys",
    "p4x_special_contacts",
    "p4x_fees",
    "client_user_agents",
]


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=text("now()"),
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=text("now()"),
            ),
        )
        op.execute(
            f"CREATE TRIGGER {table}_set_updated_at "
            f"BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_set_updated_at ON {table}")
        op.drop_column(table, "updated_at")
        op.drop_column(table, "created_at")
