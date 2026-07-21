"""updated at trigger

Revision ID: 74d19e4af679
Revises: 740805d424aa
Create Date: 2026-07-21 22:42:16.075232

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "74d19e4af679"
down_revision: str | Sequence[str] | None = "740805d424aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES_WITH_UPDATED_AT = [
    "archive_dirs",
    "archive_file_comments",
    "archive_store_items",
    "contacts",
    "members",
    "p4x_accounts",
    "p4x_categories",
    "p4x_category_filters",
    "p4x_category_filter_hits",
    "p4x_partners",
    "p4x_summary_orders",
    "p4x_transactions",
    "personal_access_tokens",
    "public_gallery_images",
    "request_logs",
    "sent_emails",
    "standesdb_images",
]


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS trigger AS $$
        BEGIN
            -- clock_timestamp(), not now(): now()/CURRENT_TIMESTAMP is
            -- constant for the whole transaction, which would give every
            -- row touched by a multi-statement transaction the same
            -- updated_at. clock_timestamp() reflects the actual moment
            -- each row is written.
            NEW.updated_at = clock_timestamp();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in _TABLES_WITH_UPDATED_AT:
        op.execute(
            f"CREATE TRIGGER {table}_set_updated_at "
            f"BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in _TABLES_WITH_UPDATED_AT:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_set_updated_at ON {table}")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
