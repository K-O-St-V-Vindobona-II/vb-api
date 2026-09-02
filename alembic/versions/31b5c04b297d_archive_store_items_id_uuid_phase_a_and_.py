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

created_by's join-backfill is a single set-based UPDATE, not the batched
per-row loop the id_uuid backfills use - it copies an already-computed
value via a join rather than generating a fresh uuid7() per row, so there
is no reason to chunk it.
"""

import uuid
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
BATCH_SIZE = 5000


def _batched_uuid_backfill() -> None:
    bind = op.get_bind()
    while True:
        rows = bind.execute(
            sa.text(
                f"SELECT id FROM {TABLE} WHERE id_uuid IS NULL LIMIT :limit"  # noqa: S608
            ),
            {"limit": BATCH_SIZE},
        ).fetchall()
        if not rows:
            return
        for row in rows:
            bind.execute(
                sa.text(f"UPDATE {TABLE} SET id_uuid = :new_id WHERE id = :old_id"),  # noqa: S608
                {"new_id": uuid.uuid7(), "old_id": row.id},
            )


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- Phase A: archive_store_items' own primary key -----------------
    op.add_column(TABLE, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    _batched_uuid_backfill()
    op.alter_column(TABLE, "id_uuid", nullable=False)
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {TABLE}_id_uuid_key ON {TABLE} (id_uuid)"
        )

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
