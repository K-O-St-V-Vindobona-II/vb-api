"""p4x_category_filters and p4x_transactions final cutover

Revision ID: 59abc303eb28
Revises: a94438173fe9
Create Date: 2026-09-02 13:00:00.000000

Two independent own-PK-only Final-Cutovers, bundled because both already
completed their Phase A in earlier slices (p4x_category_filters in
d6443ece80ad, p4x_transactions in c963591a4c7a) and neither has any
outgoing FK of its own left to cut over. Every referrer that already
points at one of these tables' id_uuid column
(p4x_category_filter_hits.p4x_category_filter_id,
p4x_category_directs.p4x_transaction_id,
p4x_category_filter_hits.p4x_transaction_id) keeps working unchanged on
the way up: a PostgreSQL RENAME COLUMN updates the column an existing
foreign key points to automatically, so none of those referrer tables
need touching in upgrade().

downgrade() is a different story: reverting a table's own primary key
means dropping and recreating its pkey index, which Postgres refuses
while a live FK in another table still depends on that index - so each
referrer's FK is dropped immediately before its parent's id is reverted
and recreated immediately after, pointing at id_uuid again.

p4x_transactions.delegating_partner_arc_check does not reference id/
id_uuid at all (it only spans the four delegating_* columns), so unlike
earlier exclusive-arc cutovers in this series it survives this migration
untouched - verified against \\d p4x_transactions before and after.
"""

from typing import NamedTuple, Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "59abc303eb28"
down_revision: str | Sequence[str] | None = "a94438173fe9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("p4x_category_filters", "p4x_transactions")


class _ReferrerFk(NamedTuple):
    name: str
    child_table: str
    child_column: str
    ondelete: str
    onupdate: str


REFERRERS: dict[str, list[_ReferrerFk]] = {
    "p4x_category_filters": [
        _ReferrerFk(
            "p4x_category_filter_hits_p4x_category_filter_id_fkey",
            "p4x_category_filter_hits",
            "p4x_category_filter_id",
            "CASCADE",
            "CASCADE",
        ),
    ],
    "p4x_transactions": [
        _ReferrerFk(
            "p4x_category_directs_p4x_transaction_id_fkey",
            "p4x_category_directs",
            "p4x_transaction_id",
            "CASCADE",
            "CASCADE",
        ),
        _ReferrerFk(
            "p4x_category_filter_hits_p4x_transaction_id_fkey",
            "p4x_category_filter_hits",
            "p4x_transaction_id",
            "CASCADE",
            "CASCADE",
        ),
    ],
}


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
    """Not loss-free - a freshly created sequence has no relationship to
    any UUID that may already have circulated, same caveat as every other
    Final-Cutover downgrade in this series. Emergency rollback shortly
    after deploy only, never a production path."""
    seq_name = f"{table}_id_seq"
    for referrer in REFERRERS[table]:
        op.drop_constraint(referrer.name, referrer.child_table, type_="foreignkey")

    op.drop_constraint(f"{table}_pkey", table, type_="primary")
    op.alter_column(table, "id", new_column_name="id_uuid")
    op.add_column(table, sa.Column("id", sa.Integer(), nullable=True))
    op.execute(f"CREATE SEQUENCE {seq_name} OWNED BY {table}.id")
    op.execute(f"UPDATE {table} SET id = nextval('{seq_name}')")  # noqa: S608
    op.alter_column(
        table,
        "id",
        nullable=False,
        server_default=sa.text(f"nextval('{seq_name}'::regclass)"),
    )
    op.create_primary_key(f"{table}_pkey", table, ["id"])
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {table}_id_uuid_key ON {table} (id_uuid)"
        )

    for referrer in REFERRERS[table]:
        op.create_foreign_key(
            referrer.name,
            referrer.child_table,
            table,
            [referrer.child_column],
            ["id_uuid"],
            ondelete=referrer.ondelete,
            onupdate=referrer.onupdate,
        )


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in TABLES:
        _cutover_own_id(table)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in reversed(TABLES):
        _revert_own_id(table)
