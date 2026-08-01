"""drop unused legacy archive store item columns

Revision ID: 323f0222d0b9
Revises: e838ebf8429a
Create Date: 2026-08-01 22:40:03.287429

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "323f0222d0b9"
down_revision: str | Sequence[str] | None = "e838ebf8429a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # All three columns hold legacy-imported data (from the pre-cutover
    # SQLite system) that no current code path ever reads: original_name/
    # original_description are written by upload_file() but never
    # displayed anywhere, and backedup_at is never even written by the
    # current app (only the legacy import populated it). Confirmed via
    # full grep across app/, scripts/, tests/, and the vb-intern frontend
    # types - zero readers anywhere.
    op.drop_column("archive_store_items", "original_name")
    op.drop_column("archive_store_items", "original_description")
    op.drop_column("archive_store_items", "backedup_at")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.add_column(
        "archive_store_items", sa.Column("original_name", sa.String(), nullable=True)
    )
    op.add_column(
        "archive_store_items",
        sa.Column("original_description", sa.String(), nullable=True),
    )
    op.add_column(
        "archive_store_items",
        sa.Column("backedup_at", sa.DateTime(timezone=True), nullable=True),
    )
