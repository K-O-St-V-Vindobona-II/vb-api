"""p4x_categories and p4x_special_contacts final cutover

Revision ID: a94438173fe9
Revises: f4d8080a373f
Create Date: 2026-09-02 12:00:00.000000

Two independent own-PK-only Final-Cutovers, bundled because both already
completed their Phase A in an earlier slice (a67f0d2a4c5e) and neither has
any outgoing FK of its own left to cut over. Every referrer that already
points at one of these tables' id_uuid column
(p4x_category_directs.p4x_category_id, p4x_category_filters.p4x_category_id,
p4x_partners.p4x_specialcontact_id,
p4x_transactions.delegating_p4x_specialcontact_id) keeps working unchanged
on the way up: a PostgreSQL RENAME COLUMN updates the column an existing
foreign key points to automatically, so none of those referrer tables need
touching in upgrade().

downgrade() is a different story: reverting a table's own primary key
means dropping and recreating its pkey index, which Postgres refuses while
a live FK in another table still depends on that index - so each
referrer's FK is dropped immediately before its parent's id is reverted
and recreated immediately after, pointing at id_uuid again.

p4x_special_contacts' underlying sequence is named p4x_specialcontacts_id_seq
(no underscore before "contacts"), not the table-name-derived
p4x_special_contacts_id_seq a fresh table would get - a pre-existing naming
quirk from before this table's own naming-convention pass, kept as-is
rather than silently renamed as a side effect of this migration.
"""

from typing import NamedTuple, Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a94438173fe9"
down_revision: str | Sequence[str] | None = "f4d8080a373f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("p4x_categories", "p4x_special_contacts")

SEQUENCE_NAMES: dict[str, str] = {
    "p4x_categories": "p4x_categories_id_seq",
    "p4x_special_contacts": "p4x_specialcontacts_id_seq",
}


class _ReferrerFk(NamedTuple):
    name: str
    child_table: str
    child_column: str
    ondelete: str
    onupdate: str


REFERRERS: dict[str, list[_ReferrerFk]] = {
    "p4x_categories": [
        _ReferrerFk(
            "p4x_category_directs_p4x_category_id_fkey",
            "p4x_category_directs",
            "p4x_category_id",
            "RESTRICT",
            "CASCADE",
        ),
        _ReferrerFk(
            "p4x_category_filters_p4x_category_id_fkey",
            "p4x_category_filters",
            "p4x_category_id",
            "RESTRICT",
            "CASCADE",
        ),
    ],
    "p4x_special_contacts": [
        _ReferrerFk(
            "p4x_partners_p4x_special_contact_id_fkey",
            "p4x_partners",
            "p4x_specialcontact_id",
            "RESTRICT",
            "CASCADE",
        ),
        _ReferrerFk(
            "p4x_transactions_delegating_p4x_specialcontact_id_fkey",
            "p4x_transactions",
            "delegating_p4x_specialcontact_id",
            "SET NULL",
            "CASCADE",
        ),
    ],
}


def _cutover_own_id(table: str) -> None:
    op.drop_constraint(f"{table}_pkey", table, type_="primary")
    op.drop_column(table, "id")
    op.execute(f"DROP SEQUENCE IF EXISTS {SEQUENCE_NAMES[table]}")
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
    seq_name = SEQUENCE_NAMES[table]
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
