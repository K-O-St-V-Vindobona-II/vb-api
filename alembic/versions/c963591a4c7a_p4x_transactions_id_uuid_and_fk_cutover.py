"""p4x_transactions id_uuid and fk cutover

Revision ID: c963591a4c7a
Revises: d6443ece80ad
Create Date: 2026-09-01 22:13:00.000000

Phase A (additive prep) for p4x_transactions' own primary key, plus the
Final-Cutover of all five of its FK columns onto the target's id_uuid:
p4x_account_id -> p4x_accounts.id_uuid, delegating_member_id ->
members.id_uuid, delegating_contact_id -> contacts.id_uuid,
delegating_p4x_account_id -> p4x_accounts.id_uuid,
delegating_p4x_specialcontact_id -> p4x_special_contacts.id_uuid.

p4x_transactions.id itself stays the primary key here - its own
Final-Cutover (dropping id in favor of id_uuid) is a later slice, since
p4x_category_directs and p4x_category_filter_hits still reference it by
the integer id and haven't cut over yet.

p4x_account_id keeps its original RESTRICT/CASCADE strategy (a
transaction may never be silently detached from its account); the four
delegating_* columns keep SET NULL/CASCADE, unchanged from before.

The table-level exclusive-arc CHECK constraint
(p4x_transactions_delegating_partner_arc_check) references all four
delegating_* columns by name - Postgres drops it automatically the
moment any one of them is dropped, so it is recreated once at the very
end of upgrade()/downgrade(), after every column is back in its final
form for that direction, rather than per-column.
"""

import uuid
from typing import NamedTuple, Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c963591a4c7a"
down_revision: str | Sequence[str] | None = "d6443ece80ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "p4x_transactions"
BATCH_SIZE = 5000
EXCLUSIVE_ARC_CHECK_NAME = "p4x_transactions_delegating_partner_arc_check"
EXCLUSIVE_ARC_CHECK_SQL = (
    "num_nonnulls(delegating_member_id, delegating_contact_id,"
    " delegating_p4x_account_id, delegating_p4x_specialcontact_id) <= 1"
)


class _FkSpec(NamedTuple):
    """One FK column's full cutover/revert description - bundled into a
    single value so _cutover_fk()/_revert_fk() take one argument instead
    of six positional ones."""

    fk_col: str
    fk_name: str
    parent_table: str
    index_name: str
    nullable: bool
    ondelete: str


TRANSACTION_FKS = (
    _FkSpec(
        fk_col="p4x_account_id",
        fk_name="p4x_transactions_p4x_account_id_fkey",
        parent_table="p4x_accounts",
        index_name="ix_p4x_transactions_p4x_account_id",
        nullable=False,
        ondelete="RESTRICT",
    ),
    _FkSpec(
        fk_col="delegating_member_id",
        fk_name="p4x_transactions_delegating_member_id_fkey",
        parent_table="members",
        index_name="ix_p4x_transactions_delegating_member_id",
        nullable=True,
        ondelete="SET NULL",
    ),
    _FkSpec(
        fk_col="delegating_contact_id",
        fk_name="p4x_transactions_delegating_contact_id_fkey",
        parent_table="contacts",
        index_name="ix_p4x_transactions_delegating_contact_id",
        nullable=True,
        ondelete="SET NULL",
    ),
    _FkSpec(
        fk_col="delegating_p4x_account_id",
        fk_name="p4x_transactions_delegating_p4x_account_id_fkey",
        parent_table="p4x_accounts",
        index_name="ix_p4x_transactions_delegating_p4x_account_id",
        nullable=True,
        ondelete="SET NULL",
    ),
    _FkSpec(
        fk_col="delegating_p4x_specialcontact_id",
        fk_name="p4x_transactions_delegating_p4x_specialcontact_id_fkey",
        parent_table="p4x_special_contacts",
        index_name="ix_p4x_transactions_delegating_p4x_specialcontact_id",
        nullable=True,
        ondelete="SET NULL",
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


def _add_own_id_uuid() -> None:
    op.add_column(TABLE, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    _batched_uuid_backfill()
    op.alter_column(TABLE, "id_uuid", nullable=False)
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {TABLE}_id_uuid_key ON {TABLE} (id_uuid)"
        )


def _drop_own_id_uuid() -> None:
    op.drop_index(f"{TABLE}_id_uuid_key", table_name=TABLE)
    op.drop_column(TABLE, "id_uuid")


def _cutover_fk(spec: _FkSpec) -> None:
    tmp_col = f"{spec.fk_col}_uuid"
    op.add_column(TABLE, sa.Column(tmp_col, sa.Uuid(), nullable=True))
    op.execute(
        f"UPDATE {TABLE} t SET {tmp_col} = p.id_uuid "  # noqa: S608
        f"FROM {spec.parent_table} p WHERE t.{spec.fk_col} = p.id"
    )
    if not spec.nullable:
        op.alter_column(TABLE, tmp_col, nullable=False)
    op.drop_constraint(spec.fk_name, TABLE, type_="foreignkey")
    op.drop_index(spec.index_name, table_name=TABLE)
    op.drop_column(TABLE, spec.fk_col)
    op.alter_column(TABLE, tmp_col, new_column_name=spec.fk_col)
    op.create_index(spec.index_name, TABLE, [spec.fk_col])
    op.create_foreign_key(
        spec.fk_name,
        TABLE,
        spec.parent_table,
        [spec.fk_col],
        ["id_uuid"],
        ondelete=spec.ondelete,
        onupdate="CASCADE",
    )


def _revert_fk(spec: _FkSpec) -> None:
    tmp_col = f"{spec.fk_col}_int"
    op.add_column(TABLE, sa.Column(tmp_col, sa.Integer(), nullable=True))
    op.execute(
        f"UPDATE {TABLE} t SET {tmp_col} = p.id "  # noqa: S608
        f"FROM {spec.parent_table} p WHERE t.{spec.fk_col} = p.id_uuid"
    )
    if not spec.nullable:
        op.alter_column(TABLE, tmp_col, nullable=False)
    op.drop_constraint(spec.fk_name, TABLE, type_="foreignkey")
    op.drop_index(spec.index_name, table_name=TABLE)
    op.drop_column(TABLE, spec.fk_col)
    op.alter_column(TABLE, tmp_col, new_column_name=spec.fk_col)
    op.create_index(spec.index_name, TABLE, [spec.fk_col])
    op.create_foreign_key(
        spec.fk_name,
        TABLE,
        spec.parent_table,
        [spec.fk_col],
        ["id"],
        ondelete=spec.ondelete,
        onupdate="CASCADE",
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    _add_own_id_uuid()
    for spec in TRANSACTION_FKS:
        _cutover_fk(spec)
    op.create_check_constraint(EXCLUSIVE_ARC_CHECK_NAME, TABLE, EXCLUSIVE_ARC_CHECK_SQL)


def downgrade() -> None:
    """Downgrade schema.

    Every FK half here is loss-free (the reverse join maps back through
    the parent's id_uuid to the exact same integer values that were
    there before). The additive id_uuid on this table's own primary key
    is dropped last, also loss-free, since it was never promoted to the
    primary key in the first place.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")
    for spec in reversed(TRANSACTION_FKS):
        _revert_fk(spec)
    _drop_own_id_uuid()
    op.create_check_constraint(EXCLUSIVE_ARC_CHECK_NAME, TABLE, EXCLUSIVE_ARC_CHECK_SQL)
