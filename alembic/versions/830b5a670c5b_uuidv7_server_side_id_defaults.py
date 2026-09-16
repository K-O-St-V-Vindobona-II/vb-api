"""uuidv7 server-side id defaults

Revision ID: 830b5a670c5b
Revises: a835e7da4c4d
Create Date: 2026-09-16 16:04:17.148659

PostgreSQL 18 ships uuidv7() as a built-in SQL function (RFC 9562), so id
generation for every UUID primary key can move from the SQLAlchemy-side
default=uuid.uuid7 to a native server_default - the same architectural
choice already made for updated_at (see 74d19e4af679_updated_at_trigger.py),
just without needing a dedicated function since Postgres provides one.
This only changes the column's DEFAULT clause; no data is touched, and no
row's existing id changes.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "830b5a670c5b"
down_revision: str | Sequence[str] | None = "a835e7da4c4d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table whose id column is a UUIDv7 primary key. No FK ordering
# concerns here: this only swaps the column DEFAULT, the columns and their
# types already exist and are already UUID.
_TABLES_WITH_UUID7_ID = [
    "archive_dirs",
    "archive_files",
    "archive_file_comments",
    "archive_permissions",
    "archive_store_items",
    "sessions",
    "badges",
    "client_user_agents",
    "contacts",
    "contacts_logs",
    "keys",
    "member_change_requests",
    "members",
    "members_logs",
    "members_oauth2bindings",
    "p4x_accounts",
    "p4x_category_directs",
    "p4x_category_filter_hits",
    "p4x_category_filters",
    "p4x_categories",
    "p4x_partners",
    "p4x_special_contacts",
    "p4x_summary_orders",
    "p4x_transactions",
    "public_gallery_images",
    "public_site_about_tabs",
    "public_site_programm_hints",
    "public_site_quotes",
    "public_site_social_links",
    "request_logs",
    "scheduled_task_runs",
    "sent_emails",
    "standesdb_images",
]


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in _TABLES_WITH_UUID7_ID:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT uuidv7()")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in _TABLES_WITH_UUID7_ID:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN id DROP DEFAULT")
