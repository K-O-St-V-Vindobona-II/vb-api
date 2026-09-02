"""sessions, oauth2bindings, summary_orders id + member fk cutover

Revision ID: bc095b5fb813
Revises: a67f0d2a4c5e
Create Date: 2026-09-01 18:30:00.000000

First slice of Wave C (Layer-1-Referrer-Cutover): four structurally
identical single-members-FK tables, bundled.

sessions, members_oauth2bindings, and p4x_summary_orders are leaf tables
with no referrers of their own, so each gets its own Final-Cutover
(Phase A+C in one step, the established pattern from every Wave-A slice)
AND its members FK cut over in the same migration - no reason to spread
a leaf table's full cutover across two slices. public_gallery_images
already has a UUID primary key (see its model docstring), so only its
created_by FK is touched here.

Every member FK below now points at members(id_uuid), not members(id) -
members itself won't have a UUID primary key until its own Final-Cutover
(slice 32) - with the exact ondelete/onupdate strategy the original FK
had, nothing loosened or tightened. sessions and members_oauth2bindings
each carry a redundant plain index on `id` (leftover `index=True` next
to the primary key's own index, same finding as sent_emails in slice 4);
dropped here rather than rebuilt on the new column. members_oauth2bindings
also carries a genuine (non-redundant) plain index on `member_id`, which
DOES get rebuilt on the new column - dropping a column drops any index
defined on it, and this one earns its keep (FK columns aren't indexed
automatically by Postgres).
"""

import uuid
from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bc095b5fb813"
down_revision: str | Sequence[str] | None = "a67f0d2a4c5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BATCH_SIZE = 5000

# (table, redundant plain index to drop alongside the pkey's own index)
LEAF_TABLES = (
    ("sessions", "ix_sessions_id"),
    ("members_oauth2bindings", "ix_members_oauth2bindings_id"),
    ("p4x_summary_orders", None),
)

# (table, fk column, fk constraint name, nullable, ondelete, onupdate,
#  extra plain index to rebuild on the new column - None if there was none)
MEMBER_FKS = (
    (
        "sessions",
        "member_id",
        "sessions_member_id_fkey",
        False,
        "CASCADE",
        "CASCADE",
        None,
    ),
    (
        "members_oauth2bindings",
        "member_id",
        "members_oauth2bindings_member_id_fkey",
        False,
        "CASCADE",
        "CASCADE",
        "ix_members_oauth2bindings_member_id",
    ),
    (
        "p4x_summary_orders",
        "ordered_by",
        "p4x_summary_orders_ordered_by_fkey",
        False,
        "CASCADE",
        "CASCADE",
        None,
    ),
    (
        "public_gallery_images",
        "created_by",
        "public_gallery_images_created_by_fkey",
        True,
        "SET NULL",
        None,
        None,
    ),
)


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


def _cutover_own_id(table: str, redundant_index: str | None) -> None:
    # --- Phase A ------------------------------------------------------
    op.add_column(table, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    _batched_uuid_backfill(table)
    op.alter_column(table, "id_uuid", nullable=False)
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {table}_id_uuid_key ON {table} (id_uuid)"
        )

    # --- Phase C (no referrer table to wait for) -----------------------
    if redundant_index:
        op.execute(f"DROP INDEX IF EXISTS {redundant_index}")
    op.drop_constraint(f"{table}_pkey", table, type_="primary")
    op.drop_column(table, "id")
    op.execute(f"DROP SEQUENCE IF EXISTS {table}_id_seq")
    op.alter_column(table, "id_uuid", new_column_name="id")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {table}_pkey "
        f"PRIMARY KEY USING INDEX {table}_id_uuid_key"
    )


def _cutover_member_fk(
    table: str,
    fk_col: str,
    fk_name: str,
    *,
    nullable: bool,
    ondelete: str,
    onupdate: str | None,
    index_name: str | None,
) -> None:
    tmp_col = f"{fk_col}_uuid"
    op.add_column(table, sa.Column(tmp_col, sa.Uuid(), nullable=True))
    op.execute(
        f"UPDATE {table} a SET {tmp_col} = m.id_uuid "  # noqa: S608
        f"FROM members m WHERE a.{fk_col} = m.id"
    )
    op.drop_constraint(fk_name, table, type_="foreignkey")
    op.drop_column(table, fk_col)
    op.alter_column(table, tmp_col, new_column_name=fk_col)
    if not nullable:
        op.alter_column(table, fk_col, nullable=False)
    if index_name:
        op.create_index(index_name, table, [fk_col])
    op.create_foreign_key(
        fk_name,
        table,
        "members",
        [fk_col],
        ["id_uuid"],
        ondelete=ondelete,
        onupdate=onupdate,
    )


def _revert_member_fk(
    table: str,
    fk_col: str,
    fk_name: str,
    *,
    nullable: bool,
    ondelete: str,
    onupdate: str | None,
    index_name: str | None,
) -> None:
    tmp_col = f"{fk_col}_int"
    op.add_column(table, sa.Column(tmp_col, sa.Integer(), nullable=True))
    op.execute(
        f"UPDATE {table} a SET {tmp_col} = m.id "  # noqa: S608
        f"FROM members m WHERE a.{fk_col} = m.id_uuid"
    )
    op.drop_constraint(fk_name, table, type_="foreignkey")
    op.drop_column(table, fk_col)
    op.alter_column(table, tmp_col, new_column_name=fk_col)
    if not nullable:
        op.alter_column(table, fk_col, nullable=False)
    if index_name:
        op.create_index(index_name, table, [fk_col])
    op.create_foreign_key(
        fk_name,
        table,
        "members",
        [fk_col],
        ["id"],
        ondelete=ondelete,
        onupdate=onupdate,
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


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table, redundant_index in LEAF_TABLES:
        _cutover_own_id(table, redundant_index)
    for (
        table,
        fk_col,
        fk_name,
        nullable,
        ondelete,
        onupdate,
        index_name,
    ) in MEMBER_FKS:
        _cutover_member_fk(
            table,
            fk_col,
            fk_name,
            nullable=nullable,
            ondelete=ondelete,
            onupdate=onupdate,
            index_name=index_name,
        )


def downgrade() -> None:
    """Downgrade schema.

    The member-FK halves are loss-free (reverse join through
    members.id_uuid back to the exact same members.id values). The three
    leaf tables' own id is NOT loss-free, same caveat as every other
    Final-Cutover downgrade in this series: a freshly created integer
    sequence has no relationship to any UUID that may already have
    circulated. Emergency rollback shortly after deploy only, never a
    production path. The redundant `id`-duplicate plain indexes dropped
    in upgrade() are intentionally not recreated here; the genuine
    members_oauth2bindings.member_id index is.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")
    for (
        table,
        fk_col,
        fk_name,
        nullable,
        ondelete,
        onupdate,
        index_name,
    ) in reversed(MEMBER_FKS):
        _revert_member_fk(
            table,
            fk_col,
            fk_name,
            nullable=nullable,
            ondelete=ondelete,
            onupdate=onupdate,
            index_name=index_name,
        )
    for table, _redundant_index in reversed(LEAF_TABLES):
        _revert_own_id(table)
