"""about_tab_slot native enum

Revision ID: 82e7d38f8fa1
Revises: fe2d67d0309b
Create Date: 2026-09-02 22:20:00.000000

public_site_about_tabs.slot was TEXT restricted by a CHECK constraint to
the 3 fixed values ('anfang', 'mkv', 'heute') this table will ever hold -
a magic-string pattern the project's native-ENUM convention (see
6f7ce9023e0e_enum_conversions_8b.py, the first slice to apply it) exists
to replace. Same technique as that migration: convert the column type in
place via ALTER COLUMN ... USING, then drop the now-redundant CHECK
(the enum type itself enforces the same restriction at a lower level, so
keeping both would just be duplicate enforcement).
"""

from typing import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "82e7d38f8fa1"
down_revision: str | None = "fe2d67d0309b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

about_tab_slot_enum = postgresql.ENUM(
    "anfang", "mkv", "heute", name="about_tab_slot", create_type=False
)


def upgrade() -> None:
    """Upgrade schema."""
    # The pre-existing UNIQUE constraint's index must go first: Postgres
    # cannot rebuild an index on the new enum type while the old text-typed
    # one is still live mid-ALTER (it errors on a missing `about_tab_slot =
    # text` operator trying to reconcile the two).
    op.drop_constraint(
        "public_site_about_tabs_slot_key", "public_site_about_tabs", type_="unique"
    )
    op.drop_constraint(
        "public_site_about_tabs_slot_check", "public_site_about_tabs", type_="check"
    )
    about_tab_slot_enum.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "public_site_about_tabs",
        "slot",
        existing_type=sa.Text(),
        type_=about_tab_slot_enum,
        existing_nullable=False,
        postgresql_using="slot::about_tab_slot",
    )
    op.create_unique_constraint(
        "public_site_about_tabs_slot_key", "public_site_about_tabs", ["slot"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "public_site_about_tabs_slot_key", "public_site_about_tabs", type_="unique"
    )
    op.alter_column(
        "public_site_about_tabs",
        "slot",
        existing_type=about_tab_slot_enum,
        type_=sa.Text(),
        existing_nullable=False,
        postgresql_using="slot::text",
    )
    about_tab_slot_enum.drop(op.get_bind(), checkfirst=True)
    op.create_check_constraint(
        "public_site_about_tabs_slot_check",
        "public_site_about_tabs",
        "slot IN ('anfang', 'mkv', 'heute')",
    )
    op.create_unique_constraint(
        "public_site_about_tabs_slot_key", "public_site_about_tabs", ["slot"]
    )
