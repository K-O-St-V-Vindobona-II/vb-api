"""sent_emails and scheduled_task_runs ids to uuid

Revision ID: 22fc473b0891
Revises: 5c9769a76def
Create Date: 2026-09-01 14:47:36.460104

Two more structurally isolated leaf tables (no incoming foreign keys),
bundled into one migration using the same Phase A + Phase C in one step,
CONCURRENTLY-index-straight-to-primary-key pattern as
61330e9e0ca8_public_site_social_links_id_to_uuid.py.

scheduled_task_runs revisits 9f8ff3c10cb2_scheduled_task_runs_id_to_
autoincrement.py, which deliberately moved this table off UUID for lack of
an enumeration-risk argument - see that migration's updated docstring for
why the schema-wide consistency decision now overrides that reasoning.

sent_emails additionally carries a redundant plain index on `id` (`ix_
sent_emails_id`, alongside the primary key's own index) predating this
migration series; dropped here rather than recreated on the new column,
since the promoted unique index already covers the same lookups.
"""

import uuid
from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "22fc473b0891"
down_revision: str | Sequence[str] | None = "5c9769a76def"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("sent_emails", "scheduled_task_runs")
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


def _cutover_to_uuid(table: str) -> None:
    # --- Phase A: additive prep -------------------------------------------
    op.add_column(table, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    _batched_uuid_backfill(table)
    op.alter_column(table, "id_uuid", nullable=False)

    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {table}_id_uuid_key ON {table} (id_uuid)"
        )

    # --- Phase C: final cutover (no referrer table to wait for) -----------
    op.drop_constraint(f"{table}_pkey", table, type_="primary")
    op.drop_column(table, "id")
    op.execute(f"DROP SEQUENCE IF EXISTS {table}_id_seq")
    op.alter_column(table, "id_uuid", new_column_name="id")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {table}_pkey "
        f"PRIMARY KEY USING INDEX {table}_id_uuid_key"
    )


def _revert_to_integer(table: str) -> None:
    op.add_column(table, sa.Column("id_int", sa.Integer(), nullable=True))
    op.execute(f"CREATE SEQUENCE {table}_id_seq OWNED BY {table}.id_int")
    op.execute(f"UPDATE {table} SET id_int = nextval('{table}_id_seq')")  # noqa: S608
    op.alter_column(
        table,
        "id_int",
        nullable=False,
        server_default=sa.text(f"nextval('{table}_id_seq'::regclass)"),
    )

    op.drop_constraint(f"{table}_pkey", table, type_="primary")
    op.drop_column(table, "id")
    op.alter_column(table, "id_int", new_column_name="id")
    op.create_primary_key(f"{table}_pkey", table, ["id"])


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    # IF EXISTS: a downgrade()/upgrade() round-trip never recreates this
    # redundant index (see downgrade()'s docstring), so a second upgrade()
    # must not fail on its absence.
    op.execute("DROP INDEX IF EXISTS ix_sent_emails_id")
    for table in TABLES:
        _cutover_to_uuid(table)


def downgrade() -> None:
    """Downgrade schema.

    Not loss-free - same caveat as every other Final-Cutover migration in
    this series: a freshly created integer sequence has no relationship to
    any UUID that may already have circulated. Emergency rollback shortly
    after deploy only, never a production path. The redundant `ix_sent_
    emails_id` index dropped in upgrade() is intentionally not recreated.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in reversed(TABLES):
        _revert_to_integer(table)
