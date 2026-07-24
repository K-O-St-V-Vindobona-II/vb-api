"""p4x category directs active unique index

Revision ID: 20db5b81733c
Revises: 42df0f369b0b
Create Date: 2026-07-24 14:51:00.427705

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20db5b81733c"
down_revision: str | Sequence[str] | None = "42df0f369b0b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # Closes a race condition: two concurrent set-category-direct requests
    # for the same transaction/category pair could otherwise both succeed.
    # Verified against dev beforehand: no existing active duplicates. Check
    # prod the same way before deploying this there.
    op.create_index(
        "p4x_category_directs_tx_category_active_uniq",
        "p4x_category_directs",
        ["p4x_transaction_id", "p4x_category_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.drop_index(
        "p4x_category_directs_tx_category_active_uniq",
        table_name="p4x_category_directs",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
