"""p4x_accounts final cutover

Revision ID: bfe999a46f89
Revises: 2727a9187d6e
Create Date: 2026-09-02 15:00:00.000000

Own-PK-only Final-Cutover: p4x_accounts completed its Phase A in an
earlier slice and has no outgoing FK of its own left to cut over. All
four referrers (p4x_category_filters.p4x_account_id,
p4x_partners.p4x_account_id, p4x_transactions.p4x_account_id,
p4x_transactions.delegating_p4x_account_id) already point at id_uuid, so
a PostgreSQL RENAME COLUMN updates the column their existing foreign keys
point to automatically - none of those referrer tables need touching in
upgrade().

downgrade() is a different story: reverting p4x_accounts' own primary key
means dropping and recreating its pkey index, which Postgres refuses
while a live FK in another table still depends on that index - so every
referrer's FK is dropped immediately before id is reverted and recreated
immediately after, pointing at id_uuid again.
"""

from typing import NamedTuple, Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bfe999a46f89"
down_revision: str | Sequence[str] | None = "2727a9187d6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "p4x_accounts"
SEQUENCE_NAME = "p4x_accounts_id_seq"


class _ReferrerFk(NamedTuple):
    name: str
    child_table: str
    child_column: str
    ondelete: str
    onupdate: str


REFERRERS: list[_ReferrerFk] = [
    _ReferrerFk(
        "p4x_category_filters_p4x_account_id_fkey",
        "p4x_category_filters",
        "p4x_account_id",
        "CASCADE",
        "CASCADE",
    ),
    _ReferrerFk(
        "p4x_partners_p4x_account_id_fkey",
        "p4x_partners",
        "p4x_account_id",
        "RESTRICT",
        "CASCADE",
    ),
    _ReferrerFk(
        "p4x_transactions_p4x_account_id_fkey",
        "p4x_transactions",
        "p4x_account_id",
        "RESTRICT",
        "CASCADE",
    ),
    _ReferrerFk(
        "p4x_transactions_delegating_p4x_account_id_fkey",
        "p4x_transactions",
        "delegating_p4x_account_id",
        "SET NULL",
        "CASCADE",
    ),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint(f"{TABLE}_pkey", TABLE, type_="primary")
    op.drop_column(TABLE, "id")
    op.execute(f"DROP SEQUENCE IF EXISTS {SEQUENCE_NAME}")
    op.alter_column(TABLE, "id_uuid", new_column_name="id")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {TABLE}_pkey "
        f"PRIMARY KEY USING INDEX {TABLE}_id_uuid_key"
    )


def downgrade() -> None:
    """Downgrade schema.

    Not loss-free - a freshly created sequence has no relationship to any
    UUID that may already have circulated, same caveat as every other
    Final-Cutover downgrade in this series. Emergency rollback shortly
    after deploy only, never a production path.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")
    for referrer in REFERRERS:
        op.drop_constraint(referrer.name, referrer.child_table, type_="foreignkey")

    op.drop_constraint(f"{TABLE}_pkey", TABLE, type_="primary")
    op.alter_column(TABLE, "id", new_column_name="id_uuid")
    op.add_column(TABLE, sa.Column("id", sa.Integer(), nullable=True))
    op.execute(f"CREATE SEQUENCE {SEQUENCE_NAME} OWNED BY {TABLE}.id")
    op.execute(f"UPDATE {TABLE} SET id = nextval('{SEQUENCE_NAME}')")  # noqa: S608
    op.alter_column(
        TABLE,
        "id",
        nullable=False,
        server_default=sa.text(f"nextval('{SEQUENCE_NAME}'::regclass)"),
    )
    op.create_primary_key(f"{TABLE}_pkey", TABLE, ["id"])
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {TABLE}_id_uuid_key ON {TABLE} (id_uuid)"
        )

    for referrer in REFERRERS:
        op.create_foreign_key(
            referrer.name,
            referrer.child_table,
            TABLE,
            [referrer.child_column],
            ["id_uuid"],
            ondelete=referrer.ondelete,
            onupdate=referrer.onupdate,
        )
