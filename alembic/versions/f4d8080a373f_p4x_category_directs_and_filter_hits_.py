"""p4x_category_directs and filter_hits final cutover

Revision ID: f4d8080a373f
Revises: 115c679b7348
Create Date: 2026-09-02 11:00:00.000000

Both tables are leaves (nothing references their own id), so each gets
Phase A+C for its own primary key in this same migration, plus the
Referrer-Cutover of its two FK columns - p4x_transaction_id on both
tables now references p4x_transactions.id_uuid (that table's own
Final-Cutover is a later slice), p4x_category_id references
p4x_categories.id_uuid, p4x_category_filter_id references
p4x_category_filters.id_uuid. Every ondelete/onupdate strategy is kept
exactly as it was. Both tables also carry composite unique
constraints spanning their two FK columns
(p4x_category_directs_tx_category_active_uniq, a partial unique index;
transaction_category_filter_id, a plain unique constraint) that
Postgres auto-drops the moment either column is dropped - both are
rebuilt explicitly at the end of upgrade()/downgrade().
"""

import uuid
from typing import NamedTuple, Sequence

import sqlalchemy as sa

from alembic import op

BATCH_SIZE = 5000

# revision identifiers, used by Alembic.
revision: str = "f4d8080a373f"
down_revision: str | Sequence[str] | None = "115c679b7348"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class _FkSpec(NamedTuple):
    column: str
    parent_table: str
    ondelete: str
    onupdate: str
    index_name: str


DIRECTS_FKS = (
    _FkSpec(
        "p4x_transaction_id",
        "p4x_transactions",
        "CASCADE",
        "CASCADE",
        "ix_p4x_category_directs_p4x_transaction_id",
    ),
    _FkSpec(
        "p4x_category_id",
        "p4x_categories",
        "RESTRICT",
        "CASCADE",
        "ix_p4x_category_directs_p4x_category_id",
    ),
)

HITS_FKS = (
    _FkSpec(
        "p4x_transaction_id",
        "p4x_transactions",
        "CASCADE",
        "CASCADE",
        "ix_p4x_category_filter_hits_p4x_transaction_id",
    ),
    _FkSpec(
        "p4x_category_filter_id",
        "p4x_category_filters",
        "CASCADE",
        "CASCADE",
        "ix_p4x_category_filter_hits_p4x_category_filter_id",
    ),
)


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
                sa.text(f"UPDATE {table} SET id_uuid = :new_id WHERE id = :old_id"),  # noqa: S608
                {"new_id": uuid.uuid7(), "old_id": row.id},
            )


def _cutover_own_id(table: str) -> None:
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


def _revert_own_id(table: str) -> None:
    """Both tables did Phase A+C in a single migration (no earlier,
    separate Phase A slice ever populated an id_uuid column for them),
    so - unlike a pure Phase-C-only revert - this must NOT leave an
    id_uuid column behind: a fresh Integer id replaces the UUID one
    entirely, matching the down_revision's actual pre-migration shape."""
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


def _cutover_fk(table: str, fk: _FkSpec) -> None:
    tmp_col = f"{fk.column}_uuid"
    op.add_column(table, sa.Column(tmp_col, sa.Uuid(), nullable=True))
    op.execute(
        f"UPDATE {table} a SET {tmp_col} = p.id_uuid "  # noqa: S608
        f"FROM {fk.parent_table} p WHERE a.{fk.column} = p.id"
    )
    op.drop_constraint(f"{table}_{fk.column}_fkey", table, type_="foreignkey")
    op.drop_index(fk.index_name, table_name=table)
    op.drop_column(table, fk.column)
    op.alter_column(table, tmp_col, new_column_name=fk.column)
    op.alter_column(table, fk.column, nullable=False)
    op.create_index(fk.index_name, table, [fk.column])
    op.create_foreign_key(
        f"{table}_{fk.column}_fkey",
        table,
        fk.parent_table,
        [fk.column],
        ["id_uuid"],
        ondelete=fk.ondelete,
        onupdate=fk.onupdate,
    )


def _revert_fk(table: str, fk: _FkSpec) -> None:
    tmp_col = f"{fk.column}_int"
    op.add_column(table, sa.Column(tmp_col, sa.Integer(), nullable=True))
    op.execute(
        f"UPDATE {table} a SET {tmp_col} = p.id "  # noqa: S608
        f"FROM {fk.parent_table} p WHERE a.{fk.column} = p.id_uuid"
    )
    op.drop_constraint(f"{table}_{fk.column}_fkey", table, type_="foreignkey")
    op.drop_index(fk.index_name, table_name=table)
    op.drop_column(table, fk.column)
    op.alter_column(table, tmp_col, new_column_name=fk.column)
    op.alter_column(table, fk.column, nullable=False)
    op.create_index(fk.index_name, table, [fk.column])
    op.create_foreign_key(
        f"{table}_{fk.column}_fkey",
        table,
        fk.parent_table,
        [fk.column],
        ["id"],
        ondelete=fk.ondelete,
        onupdate=fk.onupdate,
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    _cutover_own_id("p4x_category_directs")
    for fk in DIRECTS_FKS:
        _cutover_fk("p4x_category_directs", fk)
    op.execute(
        "CREATE UNIQUE INDEX p4x_category_directs_tx_category_active_uniq "
        "ON p4x_category_directs (p4x_transaction_id, p4x_category_id) "
        "WHERE deleted_at IS NULL"
    )

    _cutover_own_id("p4x_category_filter_hits")
    for fk in HITS_FKS:
        _cutover_fk("p4x_category_filter_hits", fk)
    op.create_unique_constraint(
        "transaction_category_filter_id",
        "p4x_category_filter_hits",
        ["p4x_transaction_id", "p4x_category_filter_id"],
    )


def downgrade() -> None:
    """Downgrade schema.

    Every FK half is loss-free (the reverse join maps back through each
    parent's id_uuid to the exact same integer values that were there
    before). Both tables' own id is NOT loss-free - a freshly created
    sequence has no relationship to any UUID that may already have
    circulated, same caveat as every other Final-Cutover downgrade in
    this series. Emergency rollback shortly after deploy only, never a
    production path.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.drop_constraint(
        "transaction_category_filter_id", "p4x_category_filter_hits", type_="unique"
    )
    for fk in reversed(HITS_FKS):
        _revert_fk("p4x_category_filter_hits", fk)
    _revert_own_id("p4x_category_filter_hits")
    op.create_unique_constraint(
        "transaction_category_filter_id",
        "p4x_category_filter_hits",
        ["p4x_transaction_id", "p4x_category_filter_id"],
    )

    op.execute("DROP INDEX IF EXISTS p4x_category_directs_tx_category_active_uniq")
    for fk in reversed(DIRECTS_FKS):
        _revert_fk("p4x_category_directs", fk)
    _revert_own_id("p4x_category_directs")
    op.execute(
        "CREATE UNIQUE INDEX p4x_category_directs_tx_category_active_uniq "
        "ON p4x_category_directs (p4x_transaction_id, p4x_category_id) "
        "WHERE deleted_at IS NULL"
    )
