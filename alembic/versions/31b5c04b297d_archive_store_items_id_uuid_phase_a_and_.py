"""archive_store_items id_uuid phase a and created_by cutover

Revision ID: 31b5c04b297d
Revises: 03e35e3c34bc
Create Date: 2026-09-01 16:12:00.000000

Two things bundled for archive_store_items, the fourth Layer-0-Parent:

1. Phase A (additive prep) on its own primary key - same pattern as every
   other Layer-0-Parent so far. archive_files cuts over onto this in its
   own slice later.
2. The first real Phase B Referrer-Cutover in this migration series:
   created_by -> members.id, added back in slice 5's Phase A prep, now
   gets its matching application-code cutover. The new FK points at
   members(id_uuid) rather than members(id) - members itself won't have
   a UUID primary key until its own Final-Cutover (slice 32), so every
   referrer joins against the parent's additive id_uuid column until then.

Both backfills are single set-based UPDATE statements: the id_uuid values
come from uuidv7(), created_by is copied from the parent through a join.
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "31b5c04b297d"
down_revision: str | Sequence[str] | None = "03e35e3c34bc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "archive_store_items"
FK_NAME = "archive_store_items_created_by_fkey"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- Phase A: archive_store_items' own primary key -----------------
    op.add_column(TABLE, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    op.execute(f"UPDATE {TABLE} SET id_uuid = uuidv7() WHERE id_uuid IS NULL")  # noqa: S608
    op.alter_column(TABLE, "id_uuid", nullable=False)
    # A plain (not CONCURRENTLY) build keeps the revision in one transaction: it
    # applies completely or not at all, and the lock timeout stays in force.
    op.create_index(f"{TABLE}_id_uuid_key", TABLE, ["id_uuid"], unique=True)

    # --- Phase B: created_by -> members.id_uuid -------------------------
    op.add_column(TABLE, sa.Column("created_by_uuid", sa.Uuid(), nullable=True))
    op.execute(
        f"UPDATE {TABLE} a SET created_by_uuid = m.id_uuid "  # noqa: S608
        "FROM members m WHERE a.created_by = m.id"
    )
    op.drop_constraint(FK_NAME, TABLE, type_="foreignkey")
    op.drop_column(TABLE, "created_by")
    op.alter_column(TABLE, "created_by_uuid", new_column_name="created_by")
    op.create_foreign_key(
        FK_NAME,
        TABLE,
        "members",
        ["created_by"],
        ["id_uuid"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def downgrade() -> None:
    """Downgrade schema.

    Both halves are loss-free: created_by's reverse join maps back through
    members.id_uuid to the exact same members.id values it came from (no
    fresh sequence involved, unlike a Final-Cutover downgrade), and
    id_uuid is purely additive - the original integer `id` was never
    touched.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- Revert Phase B ---------------------------------------------------
    op.add_column(TABLE, sa.Column("created_by_int", sa.Integer(), nullable=True))
    op.execute(
        f"UPDATE {TABLE} a SET created_by_int = m.id "  # noqa: S608
        "FROM members m WHERE a.created_by = m.id_uuid"
    )
    op.drop_constraint(FK_NAME, TABLE, type_="foreignkey")
    op.drop_column(TABLE, "created_by")
    op.alter_column(TABLE, "created_by_int", new_column_name="created_by")
    op.create_foreign_key(
        FK_NAME,
        TABLE,
        "members",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )

    # --- Revert Phase A -----------------------------------------------
    op.drop_index(f"{TABLE}_id_uuid_key", table_name=TABLE)
    op.drop_column(TABLE, "id_uuid")
