"""archive_dirs id_uuid phase a

Revision ID: 03e35e3c34bc
Revises: 53b60b0b8bbb
Create Date: 2026-09-01 16:05:00.000000

Phase A (additive prep) of the schema-wide UUID-PK migration for
archive_dirs, a third Layer-0-Parent - same pattern as
53b60b0b8bbb_contacts_id_uuid_phase_a.py. Only the real primary key gets
`id_uuid` here; the self-referencing `archive_dir_id` column (currently a
bare integer with a `0` sentinel for "root", no real FK) is deliberately
left untouched until its Final-Cutover in slice 28, which also converts
that sentinel to NULL - doing it piecemeal here would leave a confusing
half-migrated state. No API contract changes: the old integer `id` stays
the primary key.
"""

import uuid
from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "03e35e3c34bc"
down_revision: str | Sequence[str] | None = "53b60b0b8bbb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "archive_dirs"
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
    op.add_column(TABLE, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    _batched_uuid_backfill()
    op.alter_column(TABLE, "id_uuid", nullable=False)

    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {TABLE}_id_uuid_key ON {TABLE} (id_uuid)"
        )


def downgrade() -> None:
    """Downgrade schema.

    Loss-free, unlike a Final-Cutover downgrade: `id_uuid` is purely
    additive here, the original integer `id` was never touched.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_index(f"{TABLE}_id_uuid_key", table_name=TABLE)
    op.drop_column(TABLE, "id_uuid")
