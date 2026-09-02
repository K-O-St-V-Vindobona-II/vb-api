"""archive_files phase a + store item cutover, archive_permissions cutover

Revision ID: 673aa46dc3b3
Revises: 191cba0a57bd
Create Date: 2026-09-01 19:00:00.000000

Slices 15+16 bundled - same archive domain, same additive/join-backfill
pattern as every prior slice in this series.

archive_files (its own Final-Cutover follows in a later slice, once every
other referrer of its own future id has been considered):
1. Phase A on its own primary key - additive id_uuid, batched backfill,
   CONCURRENTLY-built unique index. The self-referencing-style
   `archive_dir_id` sentinel column is deliberately left untouched, same
   reasoning as archive_dirs' own Phase A migration.
2. archive_store_item_id -> archive_store_items.id_uuid Phase B cutover,
   with the identical RESTRICT/CASCADE strategy the original FK had.
   Neither column carries a secondary index beyond its own constraint (
   verified against the live schema before writing this migration), so
   there is no index to rebuild here.

archive_permissions is a leaf table - nothing references
archive_permissions.id - so it gets Phase A+C for its own primary key in
one step, the established pattern for every leaf table in this series,
plus its one real FK cutover: archive_dir_id -> archive_dirs.id_uuid,
with the identical CASCADE/CASCADE strategy the original FK had. Neither
its own `id` nor `archive_dir_id` carries a secondary index today. The
optional org_id/state_id FK hardening for this table is deliberately out
of scope here, to keep this migration focused on the UUID cutover alone.
"""

import uuid
from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "673aa46dc3b3"
down_revision: str | Sequence[str] | None = "191cba0a57bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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


def _add_phase_a_column(table: str) -> None:
    op.add_column(table, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    _batched_uuid_backfill(table)
    op.alter_column(table, "id_uuid", nullable=False)
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {table}_id_uuid_key ON {table} (id_uuid)"
        )


def _cutover_own_id(table: str) -> None:
    op.drop_constraint(f"{table}_pkey", table, type_="primary")
    op.drop_column(table, "id")
    op.execute(f"DROP SEQUENCE IF EXISTS {table}_id_seq")
    op.alter_column(table, "id_uuid", new_column_name="id")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {table}_pkey "
        f"PRIMARY KEY USING INDEX {table}_id_uuid_key"
    )


def _revert_own_id(table: str) -> None:
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


def _remove_phase_a_column(table: str) -> None:
    op.drop_index(f"{table}_id_uuid_key", table_name=table)
    op.drop_column(table, "id_uuid")


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- archive_files: Phase A on its own primary key ------------------
    _add_phase_a_column("archive_files")

    # --- archive_files: archive_store_item_id -> archive_store_items.id_uuid
    op.add_column(
        "archive_files",
        sa.Column("archive_store_item_id_uuid", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE archive_files f SET archive_store_item_id_uuid = s.id_uuid "
        "FROM archive_store_items s WHERE f.archive_store_item_id = s.id"
    )
    op.drop_constraint(
        "archive_files_archive_store_item_id_fkey", "archive_files", type_="foreignkey"
    )
    op.drop_column("archive_files", "archive_store_item_id")
    op.alter_column(
        "archive_files",
        "archive_store_item_id_uuid",
        new_column_name="archive_store_item_id",
    )
    op.alter_column("archive_files", "archive_store_item_id", nullable=False)
    op.create_foreign_key(
        "archive_files_archive_store_item_id_fkey",
        "archive_files",
        "archive_store_items",
        ["archive_store_item_id"],
        ["id_uuid"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )

    # --- archive_permissions: leaf table, Phase A+C for its own primary key
    _add_phase_a_column("archive_permissions")
    _cutover_own_id("archive_permissions")

    # --- archive_permissions: archive_dir_id -> archive_dirs.id_uuid ----
    op.add_column(
        "archive_permissions",
        sa.Column("archive_dir_id_uuid", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE archive_permissions p SET archive_dir_id_uuid = d.id_uuid "
        "FROM archive_dirs d WHERE p.archive_dir_id = d.id"
    )
    op.drop_constraint(
        "archive_permissions_archive_dir_id_fkey",
        "archive_permissions",
        type_="foreignkey",
    )
    op.drop_column("archive_permissions", "archive_dir_id")
    op.alter_column(
        "archive_permissions",
        "archive_dir_id_uuid",
        new_column_name="archive_dir_id",
    )
    op.alter_column("archive_permissions", "archive_dir_id", nullable=False)
    op.create_foreign_key(
        "archive_permissions_archive_dir_id_fkey",
        "archive_permissions",
        "archive_dirs",
        ["archive_dir_id"],
        ["id_uuid"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )


def downgrade() -> None:
    """Downgrade schema.

    Every join-backfilled FK half here is loss-free (the reverse join
    maps back through the parent's id_uuid to the exact same integer
    values that were there before). archive_permissions' own id is NOT
    loss-free - a freshly created integer sequence has no relationship to
    any UUID that may already have circulated - same caveat as every
    other Final-Cutover downgrade in this series, emergency rollback only.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- Revert archive_permissions.archive_dir_id ----------------------
    op.add_column(
        "archive_permissions",
        sa.Column("archive_dir_id_int", sa.Integer(), nullable=True),
    )
    op.execute(
        "UPDATE archive_permissions p SET archive_dir_id_int = d.id "
        "FROM archive_dirs d WHERE p.archive_dir_id = d.id_uuid"
    )
    op.drop_constraint(
        "archive_permissions_archive_dir_id_fkey",
        "archive_permissions",
        type_="foreignkey",
    )
    op.drop_column("archive_permissions", "archive_dir_id")
    op.alter_column(
        "archive_permissions",
        "archive_dir_id_int",
        new_column_name="archive_dir_id",
    )
    op.alter_column("archive_permissions", "archive_dir_id", nullable=False)
    op.create_foreign_key(
        "archive_permissions_archive_dir_id_fkey",
        "archive_permissions",
        "archive_dirs",
        ["archive_dir_id"],
        ["id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )

    # --- Revert archive_permissions' own id -----------------------------
    _revert_own_id("archive_permissions")

    # --- Revert archive_files.archive_store_item_id ---------------------
    op.add_column(
        "archive_files",
        sa.Column("archive_store_item_id_int", sa.Integer(), nullable=True),
    )
    op.execute(
        "UPDATE archive_files f SET archive_store_item_id_int = s.id "
        "FROM archive_store_items s WHERE f.archive_store_item_id = s.id_uuid"
    )
    op.drop_constraint(
        "archive_files_archive_store_item_id_fkey", "archive_files", type_="foreignkey"
    )
    op.drop_column("archive_files", "archive_store_item_id")
    op.alter_column(
        "archive_files",
        "archive_store_item_id_int",
        new_column_name="archive_store_item_id",
    )
    op.alter_column("archive_files", "archive_store_item_id", nullable=False)
    op.create_foreign_key(
        "archive_files_archive_store_item_id_fkey",
        "archive_files",
        "archive_store_items",
        ["archive_store_item_id"],
        ["id"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )

    # --- Revert archive_files' own Phase A prep column -------------------
    _remove_phase_a_column("archive_files")
