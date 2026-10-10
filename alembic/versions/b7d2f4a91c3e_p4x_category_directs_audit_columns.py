"""p4x category directs audit columns

Revision ID: b7d2f4a91c3e
Revises: e7a9c1b3d5f6
Create Date: 2026-10-10 12:00:00.000000

p4x_category_directs is an entity of its own (surrogate id, an amount, a
soft-delete column), not a mapping table, and it is the one such table
without created_at/updated_at. Existing rows get the run time of this
migration, the same accepted limitation as in 40cd764a92e7. updated_at is
kept by the shared set_updated_at() trigger.
"""

from typing import Sequence

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2f4a91c3e"
down_revision: str | Sequence[str] | None = "e7a9c1b3d5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "p4x_category_directs"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for column in ("created_at", "updated_at"):
        op.add_column(
            TABLE,
            sa.Column(
                column,
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=text("now()"),
            ),
        )
    op.execute(
        f"CREATE TRIGGER {TABLE}_set_updated_at "
        f"BEFORE UPDATE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_set_updated_at ON {TABLE}")
    op.drop_column(TABLE, "updated_at")
    op.drop_column(TABLE, "created_at")
