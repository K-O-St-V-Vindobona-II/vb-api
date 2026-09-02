"""index uncovered fk columns

Revision ID: fdd134a00dd3
Revises: 82e7d38f8fa1
Create Date: 2026-09-02 23:00:00.000000

27 FK columns across 16 tables had no covering index - found via a direct
pg_constraint/pg_index sweep (not a model-level grep), independent of the
Integer-PK -> UUID migration (this gap predates it and applies equally to
FK columns that were never touched by that project, e.g. members.org_id).

A missing index on a FK column costs two things: application queries
filtering by that column fall back to a sequential scan, and - more
importantly - every DELETE/UPDATE of a referenced parent row forces
Postgres to sequentially scan the *entire* child table to find matching
rows for the RESTRICT/CASCADE/SET NULL check, regardless of how many (or
how few) child rows actually reference it. members_logs alone already
has 15750+ rows and grows with every edit; deleting a member currently
means a full scan of it (and several other now-indexed tables) every
time.

CREATE INDEX CONCURRENTLY (not a plain CREATE INDEX) to avoid taking an
exclusive lock on tables that see live traffic - each needs its own
autocommit block since CONCURRENTLY cannot run inside a transaction.
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fdd134a00dd3"
down_revision: str | None = "82e7d38f8fa1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) pairs, matching the established ix_<table>_<column>
# naming convention already used by every other index in this schema.
_UNINDEXED_FKS: list[tuple[str, str]] = [
    ("archive_file_comments", "archive_file_id"),
    ("archive_file_comments", "created_by"),
    ("archive_files", "archive_store_item_id"),
    ("archive_permissions", "archive_dir_id"),
    ("archive_permissions", "org_id"),
    ("archive_permissions", "state_id"),
    ("archive_store_items", "created_by"),
    ("badges_members", "badge_id"),
    ("contacts", "modified_by"),
    ("contacts", "org_id"),
    ("contacts_logs", "contact_id"),
    ("contacts_logs", "modified_by"),
    ("keys_members", "key_id"),
    ("member_change_requests", "resolved_by"),
    ("members", "modified_by"),
    ("members", "org_id"),
    ("members", "parent_id"),
    ("members", "state_id"),
    ("members_logs", "member_id"),
    ("members_logs", "modified_by"),
    ("members_roles", "role_id"),
    ("p4x_summary_orders", "ordered_by"),
    ("public_gallery_images", "created_by"),
    ("sessions", "member_id"),
    ("standesdb_images", "created_by"),
    ("standesdb_images", "owner_contact_id"),
    ("standesdb_images", "owner_member_id"),
]


def upgrade() -> None:
    """Upgrade schema."""
    with op.get_context().autocommit_block():
        for table, column in _UNINDEXED_FKS:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_{table}_{column} "
                f"ON {table} ({column})"
            )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        for table, column in reversed(_UNINDEXED_FKS):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS ix_{table}_{column}")
