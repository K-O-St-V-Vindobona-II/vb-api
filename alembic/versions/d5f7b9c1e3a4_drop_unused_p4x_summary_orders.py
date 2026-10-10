"""drop the unused p4x_summary_orders table

Revision ID: d5f7b9c1e3a4
Revises: 30f721a1412a
Create Date: 2026-10-11 11:00:00.000000

The table (an order for a summary export with a recipient address and a
process id) has no reader and no writer anywhere in the application: the
summary export runs synchronously without an order. It holds e-mail addresses
of past orders, so leaving it in place keeps personal data nobody uses.

The upgrade deletes the rows with the table. The downgrade is NOT loss-free:
it recreates the table with the same columns, constraints, index and trigger
but without any row; restore the rows from a backup if they are needed.
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5f7b9c1e3a4"
down_revision: str | Sequence[str] | None = "30f721a1412a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "p4x_summary_orders"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_table(TABLE)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("ordered_by", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("summary_start", sa.Date(), nullable=False),
        sa.Column("summary_end", sa.Date(), nullable=False),
        sa.Column("pid", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_ok", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=f"{TABLE}_pkey"),
        sa.ForeignKeyConstraint(
            ["ordered_by"],
            ["members.id"],
            name=f"{TABLE}_ordered_by_fkey",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "summary_end >= summary_start",
            name=f"{TABLE}_summary_start_end_check",
        ),
    )
    op.create_index(f"ix_{TABLE}_ordered_by", TABLE, ["ordered_by"])
    op.execute(
        f"CREATE TRIGGER {TABLE}_set_updated_at "
        f"BEFORE UPDATE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
