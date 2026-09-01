"""standesdb images id uuid and fk cutover

Revision ID: 2de017d723c6
Revises: e186839a1e80
Create Date: 2026-09-02 09:00:00.000000

standesdb_images is a leaf table (nothing references standesdb_images.id),
so it gets Phase A+C for its own primary key in one step, the established
pattern for every leaf table in this series, plus all three of its FK
cutovers in the same migration: owner_member_id -> members.id_uuid,
owner_contact_id -> contacts.id_uuid (both Referrer-Cutovers of an
existing FK, CASCADE/CASCADE unchanged), and created_by -> members.id_uuid
(a genuinely missing FK added directly, same as slice 20's
contacts_logs/members_logs.modified_by and slice 22's request_logs FKs).

The table-level exclusive-arc CHECK constraint
(standesdb_images_owner_exclusive_arc_check) and the partial unique index
(standesdb_images_owner_hash_active_uniq) both reference owner_member_id/
owner_contact_id by name - Postgres drops both automatically the moment
either column is dropped, so they are recreated once at the very end of
upgrade()/downgrade(), after every column is back in its final form for
that direction, rather than per-column.

ix_standesdb_images_id (a redundant plain index alongside the primary
key, model config predates this migration) is deliberately not rebuilt
on the renamed id column - same call as sent_emails in slice 4.
"""

import uuid
from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2de017d723c6"
down_revision: str | Sequence[str] | None = "e186839a1e80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "standesdb_images"
BATCH_SIZE = 5000
EXCLUSIVE_ARC_CHECK_NAME = "standesdb_images_owner_exclusive_arc_check"
EXCLUSIVE_ARC_CHECK_SQL = (
    "(owner_member_id IS NOT NULL AND owner_contact_id IS NULL) "
    "OR (owner_member_id IS NULL AND owner_contact_id IS NOT NULL)"
)
OWNER_HASH_UNIQUE_INDEX_NAME = "standesdb_images_owner_hash_active_uniq"


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


def _cutover_own_id() -> None:
    op.add_column(TABLE, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    _batched_uuid_backfill()
    op.alter_column(TABLE, "id_uuid", nullable=False)
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {TABLE}_id_uuid_key ON {TABLE} (id_uuid)"
        )
    op.drop_index("ix_standesdb_images_id", table_name=TABLE)
    op.drop_constraint(f"{TABLE}_pkey", TABLE, type_="primary")
    op.drop_column(TABLE, "id")
    op.execute(f"DROP SEQUENCE IF EXISTS {TABLE}_id_seq")
    op.alter_column(TABLE, "id_uuid", new_column_name="id")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {TABLE}_pkey "
        f"PRIMARY KEY USING INDEX {TABLE}_id_uuid_key"
    )


def _revert_own_id() -> None:
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
    op.create_index("ix_standesdb_images_id", TABLE, ["id"])


def _cutover_nullable_fk(fk_col: str, fk_name: str, parent_table: str) -> None:
    tmp_col = f"{fk_col}_uuid"
    op.add_column(TABLE, sa.Column(tmp_col, sa.Uuid(), nullable=True))
    op.execute(
        f"UPDATE {TABLE} t SET {tmp_col} = p.id_uuid "  # noqa: S608
        f"FROM {parent_table} p WHERE t.{fk_col} = p.id"
    )
    op.drop_constraint(fk_name, TABLE, type_="foreignkey")
    op.drop_column(TABLE, fk_col)
    op.alter_column(TABLE, tmp_col, new_column_name=fk_col)
    op.create_foreign_key(
        fk_name,
        TABLE,
        parent_table,
        [fk_col],
        ["id_uuid"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )


def _revert_nullable_fk(fk_col: str, fk_name: str, parent_table: str) -> None:
    tmp_col = f"{fk_col}_int"
    op.add_column(TABLE, sa.Column(tmp_col, sa.Integer(), nullable=True))
    op.execute(
        f"UPDATE {TABLE} t SET {tmp_col} = p.id "  # noqa: S608
        f"FROM {parent_table} p WHERE t.{fk_col} = p.id_uuid"
    )
    op.drop_constraint(fk_name, TABLE, type_="foreignkey")
    op.drop_column(TABLE, fk_col)
    op.alter_column(TABLE, tmp_col, new_column_name=fk_col)
    op.create_foreign_key(
        fk_name,
        TABLE,
        parent_table,
        [fk_col],
        ["id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )


def _cutover_created_by() -> None:
    op.add_column(TABLE, sa.Column("created_by_uuid", sa.Uuid(), nullable=True))
    op.execute(
        f"UPDATE {TABLE} t SET created_by_uuid = m.id_uuid "  # noqa: S608
        f"FROM members m WHERE t.created_by = m.id"
    )
    op.drop_column(TABLE, "created_by")
    op.alter_column(TABLE, "created_by_uuid", new_column_name="created_by")
    op.create_foreign_key(
        "standesdb_images_created_by_fkey",
        TABLE,
        "members",
        ["created_by"],
        ["id_uuid"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def _revert_created_by() -> None:
    op.add_column(TABLE, sa.Column("created_by_int", sa.Integer(), nullable=True))
    op.execute(
        f"UPDATE {TABLE} t SET created_by_int = m.id "  # noqa: S608
        f"FROM members m WHERE t.created_by = m.id_uuid"
    )
    op.drop_constraint("standesdb_images_created_by_fkey", TABLE, type_="foreignkey")
    op.drop_column(TABLE, "created_by")
    op.alter_column(TABLE, "created_by_int", new_column_name="created_by")


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    _cutover_own_id()
    _cutover_nullable_fk(
        "owner_member_id", "standesdb_images_owner_member_id_fkey", "members"
    )
    _cutover_nullable_fk(
        "owner_contact_id", "standesdb_images_owner_contact_id_fkey", "contacts"
    )
    _cutover_created_by()
    op.create_check_constraint(EXCLUSIVE_ARC_CHECK_NAME, TABLE, EXCLUSIVE_ARC_CHECK_SQL)
    op.create_index(
        OWNER_HASH_UNIQUE_INDEX_NAME,
        TABLE,
        ["sha256_hash", "owner_member_id", "owner_contact_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema.

    Every FK half here is loss-free (the reverse join maps back through
    the parent's id_uuid to the exact same integer values that were
    there before). This table's own id is NOT loss-free - a freshly
    created integer sequence has no relationship to any UUID that may
    already have circulated - same caveat as every other Final-Cutover
    downgrade in this series, emergency rollback only.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_index(OWNER_HASH_UNIQUE_INDEX_NAME, table_name=TABLE)
    _revert_created_by()
    _revert_nullable_fk(
        "owner_contact_id", "standesdb_images_owner_contact_id_fkey", "contacts"
    )
    _revert_nullable_fk(
        "owner_member_id", "standesdb_images_owner_member_id_fkey", "members"
    )
    _revert_own_id()
    op.create_check_constraint(EXCLUSIVE_ARC_CHECK_NAME, TABLE, EXCLUSIVE_ARC_CHECK_SQL)
    op.create_index(
        OWNER_HASH_UNIQUE_INDEX_NAME,
        TABLE,
        ["sha256_hash", "owner_member_id", "owner_contact_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
