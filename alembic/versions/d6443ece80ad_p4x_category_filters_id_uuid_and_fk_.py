"""p4x_category_filters id_uuid and fk cutover

Revision ID: d6443ece80ad
Revises: ddc0a9d04eef
Create Date: 2026-09-01 22:00:00.000000

Phase A (additive prep) on p4x_category_filters' own primary key -
p4x_category_filter_hits references this table, so its Final-Cutover
waits for that referrer's own slice (30), same reasoning as every other
non-leaf Layer-1 table so far.

Both of its outgoing FKs are cut over in the same migration:
p4x_account_id -> p4x_accounts.id_uuid, p4x_category_id ->
p4x_categories.id_uuid, each keeping the identical CASCADE/CASCADE and
RESTRICT/CASCADE strategy it had before. Both columns carry a genuine
plain lookup index (verified against the live schema before writing
this migration), rebuilt on each cutover. The only CHECK constraint on
this table (min_amount <= max_amount) is unrelated to either FK column,
so unlike the previous slice there is nothing to recreate there.
"""

import uuid
from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6443ece80ad"
down_revision: str | Sequence[str] | None = "ddc0a9d04eef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "p4x_category_filters"
BATCH_SIZE = 5000

# (fk column, fk constraint name, parent table, plain lookup index name, ondelete)
FILTER_FKS = (
    (
        "p4x_account_id",
        "p4x_category_filters_p4x_account_id_fkey",
        "p4x_accounts",
        "ix_p4x_category_filters_p4x_account_id",
        "CASCADE",
    ),
    (
        "p4x_category_id",
        "p4x_category_filters_p4x_category_id_fkey",
        "p4x_categories",
        "ix_p4x_category_filters_p4x_category_id",
        "RESTRICT",
    ),
)


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


def _cutover_nullable_fk(
    fk_col: str, fk_name: str, parent_table: str, index_name: str, ondelete: str
) -> None:
    tmp_col = f"{fk_col}_uuid"
    op.add_column(TABLE, sa.Column(tmp_col, sa.Uuid(), nullable=True))
    op.execute(
        f"UPDATE {TABLE} t SET {tmp_col} = p.id_uuid "  # noqa: S608
        f"FROM {parent_table} p WHERE t.{fk_col} = p.id"
    )
    op.drop_constraint(fk_name, TABLE, type_="foreignkey")
    op.drop_index(index_name, table_name=TABLE)
    op.drop_column(TABLE, fk_col)
    op.alter_column(TABLE, tmp_col, new_column_name=fk_col)
    op.alter_column(TABLE, fk_col, nullable=False)
    op.create_index(index_name, TABLE, [fk_col])
    op.create_foreign_key(
        fk_name,
        TABLE,
        parent_table,
        [fk_col],
        ["id_uuid"],
        ondelete=ondelete,
        onupdate="CASCADE",
    )


def _revert_nullable_fk(
    fk_col: str, fk_name: str, parent_table: str, index_name: str, ondelete: str
) -> None:
    tmp_col = f"{fk_col}_int"
    op.add_column(TABLE, sa.Column(tmp_col, sa.Integer(), nullable=True))
    op.execute(
        f"UPDATE {TABLE} t SET {tmp_col} = p.id "  # noqa: S608
        f"FROM {parent_table} p WHERE t.{fk_col} = p.id_uuid"
    )
    op.drop_constraint(fk_name, TABLE, type_="foreignkey")
    op.drop_index(index_name, table_name=TABLE)
    op.drop_column(TABLE, fk_col)
    op.alter_column(TABLE, tmp_col, new_column_name=fk_col)
    op.alter_column(TABLE, fk_col, nullable=False)
    op.create_index(index_name, TABLE, [fk_col])
    op.create_foreign_key(
        fk_name,
        TABLE,
        parent_table,
        [fk_col],
        ["id"],
        ondelete=ondelete,
        onupdate="CASCADE",
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- Phase A: own primary key ---------------------------------------
    op.add_column(TABLE, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    _batched_uuid_backfill()
    op.alter_column(TABLE, "id_uuid", nullable=False)
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {TABLE}_id_uuid_key ON {TABLE} (id_uuid)"
        )

    # --- Phase B: both outgoing FKs --------------------------------------
    for fk_col, fk_name, parent_table, index_name, ondelete in FILTER_FKS:
        _cutover_nullable_fk(fk_col, fk_name, parent_table, index_name, ondelete)


def downgrade() -> None:
    """Downgrade schema.

    Both FK halves are loss-free (the reverse join maps back through the
    parent's id_uuid to the exact same integer values that were there
    before). id_uuid itself is purely additive - the original integer id
    was never touched, this table's own Final-Cutover is a later slice.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")

    for fk_col, fk_name, parent_table, index_name, ondelete in reversed(FILTER_FKS):
        _revert_nullable_fk(fk_col, fk_name, parent_table, index_name, ondelete)

    op.drop_index(f"{TABLE}_id_uuid_key", table_name=TABLE)
    op.drop_column(TABLE, "id_uuid")
