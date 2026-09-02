"""rename stale id_uuid not-null constraints

Revision ID: a6af1c272f2c
Revises: ce00727a65e4
Create Date: 2026-09-02 21:15:00.000000

Purely cosmetic, zero data/behavior impact: every table whose primary key
went through this project's Final-Cutover pattern (additive id_uuid
column -> RENAME COLUMN id_uuid TO id) kept a NOT NULL constraint still
named "<table>_id_uuid_not_null", because a column rename does not rename
the backing constraint's own name (Postgres tracks the FK/PK target by
attnum, but a constraint's name is just a text label that stays whatever
it was when the constraint was created). One additional case:
p4x_transactions.p4x_account_id went through the same additive-column
rename pattern during its own Referrer-Cutover, leaving the same kind of
stale name on that column's constraint too.

Left as-is, these names are actively misleading - a NOT NULL violation
on e.g. `members.id` would report the constraint "members_id_uuid_not_null",
pointing at a column that no longer exists. Renamed to match the column
they actually protect.
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6af1c272f2c"
down_revision: str | None = "ce00727a65e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) - every constraint being renamed here is
# "<table>_<column>_uuid_not_null" -> "<table>_<column>_not_null".
_RENAMES: list[tuple[str, str]] = [
    ("archive_dirs", "id"),
    ("archive_file_comments", "id"),
    ("archive_files", "id"),
    ("archive_permissions", "id"),
    ("archive_store_items", "id"),
    ("badges", "id"),
    ("client_user_agents", "id"),
    ("contacts", "id"),
    ("keys", "id"),
    ("member_change_requests", "id"),
    ("members", "id"),
    ("members_oauth2bindings", "id"),
    ("p4x_accounts", "id"),
    ("p4x_categories", "id"),
    ("p4x_category_directs", "id"),
    ("p4x_category_filter_hits", "id"),
    ("p4x_category_filters", "id"),
    ("p4x_partners", "id"),
    ("p4x_special_contacts", "id"),
    ("p4x_summary_orders", "id"),
    ("p4x_transactions", "id"),
    ("p4x_transactions", "p4x_account_id"),
    ("public_site_about_tabs", "id"),
    ("public_site_programm_hints", "id"),
    ("public_site_quotes", "id"),
    ("public_site_settings", "id"),
    ("public_site_social_links", "id"),
    ("scheduled_task_runs", "id"),
    ("sent_emails", "id"),
    ("sessions", "id"),
    ("standesdb_images", "id"),
]


def upgrade() -> None:
    """Upgrade schema."""
    for table, column in _RENAMES:
        op.execute(
            f"ALTER TABLE {table} "
            f"RENAME CONSTRAINT {table}_{column}_uuid_not_null "
            f"TO {table}_{column}_not_null"
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table, column in _RENAMES:
        op.execute(
            f"ALTER TABLE {table} "
            f"RENAME CONSTRAINT {table}_{column}_not_null "
            f"TO {table}_{column}_uuid_not_null"
        )
