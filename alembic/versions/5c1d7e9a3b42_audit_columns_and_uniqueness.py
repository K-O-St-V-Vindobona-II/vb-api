"""audit columns with server defaults, unique archive permissions and contact names

Revision ID: 5c1d7e9a3b42
Revises: 9b4c2d6e1a73
Create Date: 2026-10-09 20:00:00.000000

Three schema gaps, closed together:

1. created_at on 15 tables was nullable and had no server default, so a row
   could exist without a creation time and every insert depended on the
   application supplying it. It now defaults to now() and is NOT NULL;
   existing NULLs are filled with now(). updated_at stays as it is: NULL means
   "never updated" and the set_updated_at() trigger fills it on the first
   UPDATE. The backfill UPDATE fires that trigger for exactly the rows that
   had no created_at. The two change-log tables get only a default for
   modified_at (a NOT NULL backfill of an audit time would invent a time).
2. archive_permissions could hold the same (directory, organisation, state)
   pair twice. Duplicates are removed (the row with the smallest id stays) and
   the pair becomes unique.
3. contacts.name was UNIQUE over all rows, including soft-deleted contacts,
   while the application only checks the name among contacts that are not
   deleted (and ignoring case): re-creating a deleted contact's name ended in
   a database error. The constraint becomes a unique index on lower(name) for
   rows with deleted_at IS NULL. Two active contacts whose names differ only
   in case would block it, so the migration stops with a list of them.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c1d7e9a3b42"
down_revision: str | Sequence[str] | None = "9b4c2d6e1a73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUDITED_TABLES: tuple[str, ...] = (
    "archive_dirs",
    "archive_file_comments",
    "archive_files",
    "archive_store_items",
    "contacts",
    "members",
    "p4x_accounts",
    "p4x_categories",
    "p4x_category_filter_hits",
    "p4x_category_filters",
    "p4x_partners",
    "p4x_summary_orders",
    "p4x_transactions",
    "sent_emails",
    "standesdb_images",
)
_DEFAULT_ONLY: tuple[tuple[str, str], ...] = (
    ("contacts_logs", "modified_at"),
    ("members_logs", "modified_at"),
)


def upgrade() -> None:
    """Upgrade schema."""
    # Checked first: nothing is changed, and no lock is taken, when it fails.
    clashes = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT lower(name) FROM contacts WHERE deleted_at IS NULL "
                "GROUP BY lower(name) HAVING count(*) > 1"
            )
        )
        .scalars()
        .all()
    )
    if clashes:
        msg = f"Active contacts whose names differ only in case: {clashes}"
        raise RuntimeError(msg)

    op.execute("SET LOCAL lock_timeout = '5s'")

    for table in _AUDITED_TABLES:
        op.execute(
            f"UPDATE {table} SET created_at = now() "  # noqa: S608
            "WHERE created_at IS NULL"
        )
        op.alter_column(
            table,
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        )
    for table, column in _DEFAULT_ONLY:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        )

    op.execute(
        "DELETE FROM archive_permissions a USING archive_permissions b "
        "WHERE a.archive_dir_id = b.archive_dir_id AND a.org_id = b.org_id "
        "AND a.state_id = b.state_id AND a.id > b.id"
    )
    op.create_unique_constraint(
        "archive_permissions_dir_org_state_key",
        "archive_permissions",
        ["archive_dir_id", "org_id", "state_id"],
    )

    op.drop_constraint("contacts_name_key", "contacts", type_="unique")
    op.create_index(
        "contacts_name_active_key",
        "contacts",
        [sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.drop_index("contacts_name_active_key", table_name="contacts")
    op.create_unique_constraint("contacts_name_key", "contacts", ["name"])

    op.drop_constraint(
        "archive_permissions_dir_org_state_key", "archive_permissions", type_="unique"
    )

    for table, column in _DEFAULT_ONLY:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            server_default=None,
        )
    for table in _AUDITED_TABLES:
        op.alter_column(
            table,
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            server_default=None,
            nullable=True,
        )
