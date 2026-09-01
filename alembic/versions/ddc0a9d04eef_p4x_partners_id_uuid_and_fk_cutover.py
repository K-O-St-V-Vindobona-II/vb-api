"""p4x_partners id_uuid and fk cutover

Revision ID: ddc0a9d04eef
Revises: 673aa46dc3b3
Create Date: 2026-09-01 20:30:00.000000

p4x_partners is a leaf table - nothing references p4x_partners.id - so
it gets Phase A+C for its own primary key in one step, the established
pattern for every leaf table in this series, plus all four of its
exclusive-arc FK cutovers in the same migration: member_id ->
members.id_uuid, contact_id -> contacts.id_uuid, p4x_account_id ->
p4x_accounts.id_uuid, p4x_specialcontact_id ->
p4x_special_contacts.id_uuid. Every FK keeps the identical RESTRICT/
CASCADE strategy it had before - a P4xPartner row is a financial/
accounting link, not owned content, so a referenced entity may never be
silently detached.

Unlike the previous (archive) slice, all four FK columns here carry a
genuine plain lookup index (ix_p4x_partners_member_id and friends,
verified against the live schema before writing this migration) -
dropping each column drops its index automatically, so every cutover
rebuilds it on the new column.

The table-level exclusive-arc CHECK constraint
(p4x_partners_partner_exclusive_arc_check) references all four columns
by name - Postgres drops it automatically the moment any one of them is
dropped, so it is recreated once at the very end of upgrade()/
downgrade(), after every column is back in its final form for that
direction, rather than per-column.

p4x_transactions.delegating_member_id/delegating_contact_id/
delegating_p4x_account_id/delegating_p4x_specialcontact_id are a
structurally similar but entirely separate exclusive-arc set on a
different table, deliberately out of scope here - that table's own
UUID cutover is a later slice. The application-code cutover in this
slice's accompanying commit therefore has to bridge both id flavors for
a short transitional period (see p4x_partner_service.find_partner_entity
vs. find_partner_entity_by_legacy_id).
"""

import uuid
from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ddc0a9d04eef"
down_revision: str | Sequence[str] | None = "673aa46dc3b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "p4x_partners"
BATCH_SIZE = 5000
EXCLUSIVE_ARC_CHECK_NAME = "p4x_partners_partner_exclusive_arc_check"
EXCLUSIVE_ARC_CHECK_SQL = (
    "num_nonnulls(member_id, contact_id, p4x_account_id, p4x_specialcontact_id) = 1"
)

# (fk column, fk constraint name, parent table, plain lookup index name)
PARTNER_FKS = (
    (
        "member_id",
        "p4x_partners_member_id_fkey",
        "members",
        "ix_p4x_partners_member_id",
    ),
    (
        "contact_id",
        "p4x_partners_contact_id_fkey",
        "contacts",
        "ix_p4x_partners_contact_id",
    ),
    (
        "p4x_account_id",
        "p4x_partners_p4x_account_id_fkey",
        "p4x_accounts",
        "ix_p4x_partners_p4x_account_id",
    ),
    (
        "p4x_specialcontact_id",
        "p4x_partners_p4x_special_contact_id_fkey",
        "p4x_special_contacts",
        "ix_p4x_partners_p4x_specialcontact_id",
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


def _cutover_own_id() -> None:
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


def _cutover_nullable_fk(
    fk_col: str, fk_name: str, parent_table: str, index_name: str
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
    op.create_index(index_name, TABLE, [fk_col])
    op.create_foreign_key(
        fk_name,
        TABLE,
        parent_table,
        [fk_col],
        ["id_uuid"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )


def _revert_nullable_fk(
    fk_col: str, fk_name: str, parent_table: str, index_name: str
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
    op.create_index(index_name, TABLE, [fk_col])
    op.create_foreign_key(
        fk_name,
        TABLE,
        parent_table,
        [fk_col],
        ["id"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    _cutover_own_id()
    for fk_col, fk_name, parent_table, index_name in PARTNER_FKS:
        _cutover_nullable_fk(fk_col, fk_name, parent_table, index_name)
    op.create_check_constraint(EXCLUSIVE_ARC_CHECK_NAME, TABLE, EXCLUSIVE_ARC_CHECK_SQL)


def downgrade() -> None:
    """Downgrade schema.

    Every FK half here is loss-free (the reverse join maps back through
    the parent's id_uuid to the exact same integer values that were
    there before). p4x_partners' own id is NOT loss-free - a freshly
    created integer sequence has no relationship to any UUID that may
    already have circulated - same caveat as every other Final-Cutover
    downgrade in this series, emergency rollback only.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")
    for fk_col, fk_name, parent_table, index_name in reversed(PARTNER_FKS):
        _revert_nullable_fk(fk_col, fk_name, parent_table, index_name)
    _revert_own_id()
    op.create_check_constraint(EXCLUSIVE_ARC_CHECK_NAME, TABLE, EXCLUSIVE_ARC_CHECK_SQL)
