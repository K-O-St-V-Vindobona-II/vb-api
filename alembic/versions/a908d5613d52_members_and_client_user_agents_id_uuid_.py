"""members and client_user_agents id_uuid phase a

Revision ID: a908d5613d52
Revises: 22fc473b0891
Create Date: 2026-09-01 15:17:33.331724

Phase A (additive prep) of the schema-wide UUID-PK migration for the two
Layer-0-Parent tables that referrer tables will cut over onto next:
members (the schema's hub, 17 incoming FKs plus a self-referencing
parent_id, started this early precisely so those referrer slices can run
in parallel afterward) and client_user_agents (referenced only by
request_logs' bare, FK-less client_user_agent_id column today).

No API contract changes: the old integer `id` stays the primary key,
`id_uuid` is purely an additive, nullable-then-backfilled column. See
61330e9e0ca8_public_site_social_links_id_to_uuid.py for the Phase
A/B/C pattern this migration series follows; this one only ever reaches
Phase A, since both tables have referrers still to migrate before the
matching Final-Cutover (members: slice 32, client_user_agents: bundled
into slice 22 with request_logs).
"""

import uuid
from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a908d5613d52"
down_revision: str | Sequence[str] | None = "22fc473b0891"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("members", "client_user_agents")
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
