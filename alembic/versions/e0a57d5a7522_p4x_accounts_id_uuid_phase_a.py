"""p4x_accounts id_uuid phase a

Revision ID: e0a57d5a7522
Revises: e2f6d45fab87
Create Date: 2026-09-01 17:30:00.000000

Phase A (additive prep) of the schema-wide UUID-PK migration for
p4x_accounts, kept its own slice (rather than bundled with the other
p4x Layer-0-Parents) since its Final-Cutover in slice 27 becomes the
plan's most complex one - p4x_partners, p4x_category_filters, and
p4x_transactions all converge on it. No API contract changes: the old
integer `id` stays the primary key.
"""

import uuid
from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e0a57d5a7522"
down_revision: str | Sequence[str] | None = "e2f6d45fab87"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "p4x_accounts"
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
