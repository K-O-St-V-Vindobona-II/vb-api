"""archive_file_comments id, archive_file_id, created_by cutover

Revision ID: dd8661641df7
Revises: 2de017d723c6
Create Date: 2026-09-02 09:00:00.000000

archive_file_comments is a leaf table with no referrers of its own, so it
gets its own Final-Cutover (Phase A+C) AND both of its FKs (archive_file_id,
created_by) cut over in the same migration - same reasoning as
ec1af5390d0c's member_change_requests. archive_file_id now points at
archive_files(id_uuid), not archive_files(id) - archive_files itself won't
have a UUID primary key until its own Final-Cutover. created_by now points
at members(id_uuid), not members(id) - members itself won't have a UUID
primary key until its own Final-Cutover. Both FKs keep the exact
ondelete/onupdate strategy they had before (CASCADE/CASCADE and
SET NULL/CASCADE respectively). Neither column carries an index beyond its
own FK constraint, so no index rebuild is needed.
"""

import uuid
from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dd8661641df7"
down_revision: str | Sequence[str] | None = "2de017d723c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "archive_file_comments"
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


def _join_backfill(
    fk_col: str, tmp_col: str, *, parent_table: str, to_uuid: bool
) -> None:
    parent_col = "id" if to_uuid else "id_uuid"
    new_col = "id_uuid" if to_uuid else "id"
    op.execute(
        f"UPDATE {TABLE} a SET {tmp_col} = p.{new_col} "  # noqa: S608
        f"FROM {parent_table} p WHERE a.{fk_col} = p.{parent_col}"
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- Phase A+C: own primary key --------------------------------------
    op.add_column(TABLE, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    _batched_uuid_backfill()
    op.alter_column(TABLE, "id_uuid", nullable=False)
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {TABLE}_id_uuid_key ON {TABLE} (id_uuid)"
        )
    op.drop_constraint(f"{TABLE}_pkey", TABLE, type_="primary")
    op.drop_column(TABLE, "id")
    op.execute(f"DROP SEQUENCE IF EXISTS {TABLE}_id_seq")
    op.alter_column(TABLE, "id_uuid", new_column_name="id")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {TABLE}_pkey "
        f"PRIMARY KEY USING INDEX {TABLE}_id_uuid_key"
    )

    # --- Phase B: archive_file_id -> archive_files.id_uuid (NOT NULL) -----
    op.add_column(TABLE, sa.Column("archive_file_id_uuid", sa.Uuid(), nullable=True))
    _join_backfill(
        "archive_file_id",
        "archive_file_id_uuid",
        parent_table="archive_files",
        to_uuid=True,
    )
    op.drop_constraint(
        "archive_file_comments_archive_file_id_fkey", TABLE, type_="foreignkey"
    )
    op.drop_column(TABLE, "archive_file_id")
    op.alter_column(TABLE, "archive_file_id_uuid", new_column_name="archive_file_id")
    op.alter_column(TABLE, "archive_file_id", nullable=False)
    op.create_foreign_key(
        "archive_file_comments_archive_file_id_fkey",
        TABLE,
        "archive_files",
        ["archive_file_id"],
        ["id_uuid"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )

    # --- Phase B: created_by -> members.id_uuid (nullable) -----------------
    op.add_column(TABLE, sa.Column("created_by_uuid", sa.Uuid(), nullable=True))
    _join_backfill(
        "created_by", "created_by_uuid", parent_table="members", to_uuid=True
    )
    op.drop_constraint(
        "archive_file_comments_created_by_fkey", TABLE, type_="foreignkey"
    )
    op.drop_column(TABLE, "created_by")
    op.alter_column(TABLE, "created_by_uuid", new_column_name="created_by")
    op.create_foreign_key(
        "archive_file_comments_created_by_fkey",
        TABLE,
        "members",
        ["created_by"],
        ["id_uuid"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def downgrade() -> None:
    """Downgrade schema.

    The two FK halves are loss-free (reverse join through the parent
    table's id_uuid back to the exact same id values). The table's own id
    is NOT loss-free, same caveat as every other Final-Cutover downgrade in
    this series: a freshly created integer sequence has no relationship to
    any UUID that may already have circulated. Emergency rollback shortly
    after deploy only, never a production path.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- Revert created_by ---------------------------------------------
    op.add_column(TABLE, sa.Column("created_by_int", sa.Integer(), nullable=True))
    _join_backfill(
        "created_by", "created_by_int", parent_table="members", to_uuid=False
    )
    op.drop_constraint(
        "archive_file_comments_created_by_fkey", TABLE, type_="foreignkey"
    )
    op.drop_column(TABLE, "created_by")
    op.alter_column(TABLE, "created_by_int", new_column_name="created_by")
    op.create_foreign_key(
        "archive_file_comments_created_by_fkey",
        TABLE,
        "members",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )

    # --- Revert archive_file_id -----------------------------------------
    op.add_column(TABLE, sa.Column("archive_file_id_int", sa.Integer(), nullable=True))
    _join_backfill(
        "archive_file_id",
        "archive_file_id_int",
        parent_table="archive_files",
        to_uuid=False,
    )
    op.drop_constraint(
        "archive_file_comments_archive_file_id_fkey", TABLE, type_="foreignkey"
    )
    op.drop_column(TABLE, "archive_file_id")
    op.alter_column(TABLE, "archive_file_id_int", new_column_name="archive_file_id")
    op.alter_column(TABLE, "archive_file_id", nullable=False)
    op.create_foreign_key(
        "archive_file_comments_archive_file_id_fkey",
        TABLE,
        "archive_files",
        ["archive_file_id"],
        ["id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )

    # --- Revert own primary key --------------------------------------
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
