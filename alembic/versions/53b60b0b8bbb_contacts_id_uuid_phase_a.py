"""contacts id_uuid phase a

Revision ID: 53b60b0b8bbb
Revises: a908d5613d52
Create Date: 2026-09-01 15:35:00.000000

Phase A (additive prep) of the schema-wide UUID-PK migration for the
contacts table, the second Layer-0-Parent - same pattern as
a908d5613d52_members_and_client_user_agents_id_uuid_.py. No API contract
changes: the old integer `id` stays the primary key, `id_uuid` is purely
an additive, nullable-then-backfilled column. Final-Cutover follows in
slice 29, once every referrer table (contacts_logs, p4x_transactions,
p4x_partners, standesdb_images) has cut over onto this column.
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "53b60b0b8bbb"
down_revision: str | Sequence[str] | None = "a908d5613d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "contacts"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(TABLE, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    op.execute(f"UPDATE {TABLE} SET id_uuid = uuidv7() WHERE id_uuid IS NULL")  # noqa: S608
    op.alter_column(TABLE, "id_uuid", nullable=False)

    # A plain (not CONCURRENTLY) build keeps the revision in one transaction: it
    # applies completely or not at all, and the lock timeout stays in force.
    op.create_index(f"{TABLE}_id_uuid_key", TABLE, ["id_uuid"], unique=True)


def downgrade() -> None:
    """Downgrade schema.

    Loss-free, unlike a Final-Cutover downgrade: `id_uuid` is purely
    additive here, the original integer `id` was never touched.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_index(f"{TABLE}_id_uuid_key", table_name=TABLE)
    op.drop_column(TABLE, "id_uuid")
