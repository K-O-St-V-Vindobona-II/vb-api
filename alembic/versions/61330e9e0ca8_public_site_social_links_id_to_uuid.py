"""public_site_social_links id to uuid

Revision ID: 61330e9e0ca8
Revises: 46e5c8d03b55
Create Date: 2026-09-01 11:09:17.203015

Pattern-setter for the schema-wide Integer-PK -> UUID migration: a leaf
table with no incoming foreign keys runs Phase A (additive `id_uuid`
column, batched backfill, concurrently-built unique index) and Phase C
(final cutover: drop the old integer `id`, rename `id_uuid` to `id`,
promote the already-built unique index straight to the primary key - no
second index build needed) in one migration, since there is no referrer
table that would need a separate deploy window in between.

`id_uuid` is backfilled in Python via `uuid.uuid7()`, not a server-side
`gen_random_uuid()`/pgcrypto default, to match the SQLAlchemy model's own
`default=uuid.uuid7` used for every future insert.
"""

import uuid
from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "61330e9e0ca8"
down_revision: str | Sequence[str] | None = "46e5c8d03b55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "public_site_social_links"
BATCH_SIZE = 5000


def _batched_uuid_backfill() -> None:
    bind = op.get_bind()
    while True:
        rows = bind.execute(
            sa.text(f"SELECT id FROM {TABLE} WHERE id_uuid IS NULL LIMIT :limit"),  # noqa: S608
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

    # --- Phase A: additive prep -------------------------------------------
    op.add_column(TABLE, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    _batched_uuid_backfill()
    op.alter_column(TABLE, "id_uuid", nullable=False)

    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {TABLE}_id_uuid_key ON {TABLE} (id_uuid)"
        )

    # --- Phase C: final cutover (no referrer table to wait for) -----------
    op.drop_constraint(f"{TABLE}_pkey", TABLE, type_="primary")
    op.drop_column(TABLE, "id")
    # The old id column's sequence isn't dropped by DROP COLUMN unless it's
    # marked OWNED BY that column, which SQLAlchemy's Integer-autoincrement
    # columns aren't - drop it explicitly to avoid leaving an orphaned
    # sequence object behind.
    op.execute(f"DROP SEQUENCE IF EXISTS {TABLE}_id_seq")
    op.alter_column(TABLE, "id_uuid", new_column_name="id")
    # Promotes the index CONCURRENTLY-built above straight to the primary
    # key's backing index - a fast metadata-only operation, no second
    # index build (and no table scan/lock) required.
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {TABLE}_pkey "
        f"PRIMARY KEY USING INDEX {TABLE}_id_uuid_key"
    )


def downgrade() -> None:
    """Downgrade schema.

    Not loss-free: a freshly created integer sequence has no relationship
    to any UUID that may already have circulated (e.g. in an admin's
    browser session or logs) - emergency rollback shortly after deploy
    only, never a production path (same caveat as
    9f8ff3c10cb2_scheduled_task_runs_id_to_autoincrement.py).
    """
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.add_column(TABLE, sa.Column("id_int", sa.Integer(), nullable=True))
    op.execute(f"CREATE SEQUENCE {TABLE}_id_seq OWNED BY {TABLE}.id_int")
    op.execute(f"UPDATE {TABLE} SET id_int = nextval('{TABLE}_id_seq')")  # noqa: S608
    op.alter_column(
        TABLE,
        "id_int",
        nullable=False,
        server_default=sa.text(f"nextval('{TABLE}_id_seq'::regclass)"),
    )

    op.drop_constraint(f"{TABLE}_pkey", TABLE, type_="primary")
    op.drop_column(TABLE, "id")
    op.alter_column(TABLE, "id_int", new_column_name="id")
    op.create_primary_key(f"{TABLE}_pkey", TABLE, ["id"])
