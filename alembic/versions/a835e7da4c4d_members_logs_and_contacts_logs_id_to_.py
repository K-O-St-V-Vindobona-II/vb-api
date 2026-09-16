"""members_logs and contacts_logs id to uuid

Revision ID: a835e7da4c4d
Revises: 8f0230673ae0
Create Date: 2026-09-16 14:26:15.687903

The two remaining integer primary keys in the schema, left out of the
original UUID-PK migration alongside request_logs (see
e186839a1e80_request_logs_fk_cutover_and_client_user_.py's docstring for
the shared context - all three were "own PK, not migrated yet" at the
time). Closed now for the same reason request_logs was: no other table
holds a foreign key onto either id (both are pure changelog/audit tables),
so - like request_logs - this needs no separate Phase A / Final-Cutover
split, one migration per table is enough.
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a835e7da4c4d"
down_revision: str | Sequence[str] | None = "8f0230673ae0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("members_logs", "contacts_logs")
BATCH_SIZE = 5000


def _batched_uuid_backfill(table: str) -> None:
    bind = op.get_bind()
    while True:
        rows = bind.execute(
            sa.text(
                f"SELECT id FROM {table} WHERE id_uuid IS NULL LIMIT :limit"  # noqa: S608
            ),
            {"limit": BATCH_SIZE},
        ).fetchall()
        if not rows:
            return
        for row in rows:
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET id_uuid = :new_id WHERE id = :old_id"  # noqa: S608
                ),
                {"new_id": uuid.uuid7(), "old_id": row.id},
            )


def _cutover(table: str) -> None:
    op.add_column(table, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    _batched_uuid_backfill(table)
    op.alter_column(table, "id_uuid", nullable=False)

    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {table}_id_uuid_key ON {table} (id_uuid)"
        )

    op.drop_constraint(f"{table}_pkey", table, type_="primary")
    op.drop_column(table, "id")
    op.execute(f"DROP SEQUENCE IF EXISTS {table}_id_seq")
    op.alter_column(table, "id_uuid", new_column_name="id")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {table}_pkey "
        f"PRIMARY KEY USING INDEX {table}_id_uuid_key"
    )


def _revert(table: str) -> None:
    op.drop_constraint(f"{table}_pkey", table, type_="primary")
    op.alter_column(table, "id", new_column_name="id_uuid")
    op.add_column(table, sa.Column("id", sa.Integer(), nullable=True))
    op.execute(f"CREATE SEQUENCE {table}_id_seq OWNED BY {table}.id")
    op.execute(f"UPDATE {table} SET id = nextval('{table}_id_seq')")  # noqa: S608
    op.alter_column(
        table,
        "id",
        nullable=False,
        server_default=sa.text(f"nextval('{table}_id_seq'::regclass)"),
    )
    op.create_primary_key(f"{table}_pkey", table, ["id"])
    op.drop_column(table, "id_uuid")


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in TABLES:
        _cutover(table)


def downgrade() -> None:
    """Downgrade schema.

    Not loss-free - a freshly created sequence has no relationship to any
    UUID that may already have circulated, same caveat as every other
    Final-Cutover downgrade in this migration series, emergency rollback
    only.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in reversed(TABLES):
        _revert(table)
