"""archive_store_items, badges, keys final cutover

Revision ID: 03c2395ca34e
Revises: dd8661641df7
Create Date: 2026-09-02 10:00:00.000000

Three independent own-PK-only Final-Cutovers, bundled because all three
already completed their Phase A in earlier slices (archive_store_items in
31b5c04b297d, badges/keys in e2f6d45fab87) and none has any outgoing FK of
its own left to cut over - archive_store_items.created_by already
references members.id_uuid. Every referrer that already points at one of
these tables' id_uuid column (archive_files.archive_store_item_id,
badges_members.badge_id, keys_members.key_id) keeps working unchanged on
the way up: a PostgreSQL RENAME COLUMN updates the column an existing
foreign key points to automatically, so none of those referrer tables
need touching in upgrade().

downgrade() is a different story: reverting a table's own primary key
means dropping and recreating its pkey index, which Postgres refuses
while a live FK in another table still depends on that index - so each
referrer's FK is dropped immediately before its parent's id is reverted
and recreated immediately after, pointing at id_uuid again (restoring the
exact shape each pair had at dd8661641df7).
"""

from typing import NamedTuple, Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "03c2395ca34e"
down_revision: str | Sequence[str] | None = "dd8661641df7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("archive_store_items", "badges", "keys")


class _ReferrerFk(NamedTuple):
    name: str
    child_table: str
    child_column: str
    ondelete: str
    onupdate: str


REFERRERS: dict[str, _ReferrerFk] = {
    "archive_store_items": _ReferrerFk(
        "archive_files_archive_store_item_id_fkey",
        "archive_files",
        "archive_store_item_id",
        "RESTRICT",
        "CASCADE",
    ),
    "badges": _ReferrerFk(
        "badges_members_badge_id_fkey",
        "badges_members",
        "badge_id",
        "RESTRICT",
        "CASCADE",
    ),
    "keys": _ReferrerFk(
        "keys_members_key_id_fkey", "keys_members", "key_id", "RESTRICT", "CASCADE"
    ),
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
    referrer = REFERRERS[table]
    op.drop_constraint(referrer.name, referrer.child_table, type_="foreignkey")

    op.drop_constraint(f"{table}_pkey", table, type_="primary")
    op.alter_column(table, "id", new_column_name="id_uuid")
    op.add_column(table, sa.Column("id", sa.Integer(), nullable=True))
    op.execute(f"CREATE SEQUENCE {table}_id_seq OWNED BY {table}.id")
    op.execute(f"UPDATE {table} SET id = nextval('{table}_id_seq')")  # noqa: S608
    op.alter_column(
        table,
        "id",
        nullable=False,
        server_default=sa.text(f"nextval('{table}_id_seq'::regclass)"),
    )
    op.create_primary_key(f"{table}_pkey", table, ["id"])
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {table}_id_uuid_key ON {table} (id_uuid)"
        )

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
