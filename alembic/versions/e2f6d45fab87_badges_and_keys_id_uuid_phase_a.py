"""badges and keys id_uuid phase a

Revision ID: e2f6d45fab87
Revises: 31b5c04b297d
Create Date: 2026-09-01 16:20:00.000000

Phase A (additive prep) of the schema-wide UUID-PK migration for badges
and keys, the last two Layer-0-Parents - same bundled pattern as
a908d5613d52_members_and_client_user_agents_id_uuid_.py. No API contract
changes: the old integer `id` stays the primary key on both tables. Their
composite-PK referrer tables (badges_members, keys_members) cut over in
slice 14, once members has its own Phase A too (already done, slice 5).
"""

import uuid
from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2f6d45fab87"
down_revision: str | Sequence[str] | None = "31b5c04b297d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("badges", "keys")
BATCH_SIZE = 5000


def _batched_uuid_backfill(table: str) -> None:
    bind = op.get_bind()
    while True:
        rows = bind.execute(
            sa.text(f"SELECT id FROM {table} WHERE id_uuid IS NULL LIMIT :limit"),  # noqa: S608
            {"limit": BATCH_SIZE},
        ).fetchall()
        if not rows:
            return
        for row in rows:
            bind.execute(
                sa.text(f"UPDATE {table} SET id_uuid = :new_id WHERE id = :old_id"),  # noqa: S608
                {"new_id": uuid.uuid7(), "old_id": row.id},
            )


def _add_uuid_column(table: str) -> None:
    op.add_column(table, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    _batched_uuid_backfill(table)
    op.alter_column(table, "id_uuid", nullable=False)

    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {table}_id_uuid_key ON {table} (id_uuid)"
        )


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in TABLES:
        _add_uuid_column(table)


def downgrade() -> None:
    """Downgrade schema.

    Loss-free, unlike a Final-Cutover downgrade: `id_uuid` is purely
    additive here, the original integer `id` was never touched.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in reversed(TABLES):
        op.drop_index(f"{table}_id_uuid_key", table_name=table)
        op.drop_column(table, "id_uuid")
