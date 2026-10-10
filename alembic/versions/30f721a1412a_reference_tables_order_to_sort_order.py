"""reference tables order to sort_order

Revision ID: 30f721a1412a
Revises: 3871dffffdd0
Create Date: 2026-10-11 10:00:00.000000

orgs, roles, states and badges stored their display position in a column
called "order", an SQL reserved word that had to be quoted in every raw
statement and CHECK condition. The column is renamed to sort_order, the name
public_gallery_images already uses for the same concept, and the non-negative
CHECK constraint of each table is renamed to match. The CHECK conditions
follow the rename by themselves. The API field name (order) is unchanged.
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "30f721a1412a"
down_revision: str | Sequence[str] | None = "3871dffffdd0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("orgs", "roles", "states", "badges")


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in TABLES:
        op.alter_column(table, "order", new_column_name="sort_order")
        op.execute(
            f"ALTER TABLE {table} RENAME CONSTRAINT "
            f"{table}_order_check TO {table}_sort_order_check"
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in TABLES:
        op.execute(
            f"ALTER TABLE {table} RENAME CONSTRAINT "
            f"{table}_sort_order_check TO {table}_order_check"
        )
        op.alter_column(table, "sort_order", new_column_name="order")
