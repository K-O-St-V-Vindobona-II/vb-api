"""standesdb images default to is_default

Revision ID: 3871dffffdd0
Revises: b7d2f4a91c3e
Create Date: 2026-10-11 09:00:00.000000

standesdb_images.default carried the name of an SQL reserved word, so every
raw statement had to quote it ("default") and the column read like a keyword
instead of the boolean flag it is. It is renamed to is_default. The two
partial unique indexes that allow one default image per owner follow the
rename automatically; the NOT NULL constraint keeps its generated name, so it
is renamed to match. The API field names (default, default_image) are
unchanged.
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3871dffffdd0"
down_revision: str | Sequence[str] | None = "b7d2f4a91c3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "standesdb_images"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.alter_column(TABLE, "default", new_column_name="is_default")
    op.execute(
        f"ALTER TABLE {TABLE} RENAME CONSTRAINT "
        f"{TABLE}_default_not_null TO {TABLE}_is_default_not_null"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        f"ALTER TABLE {TABLE} RENAME CONSTRAINT "
        f"{TABLE}_is_default_not_null TO {TABLE}_default_not_null"
    )
    op.alter_column(TABLE, "is_default", new_column_name="default")
