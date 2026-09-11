"""index soft delete columns

Revision ID: a1fbacf33990
Revises: 40cd764a92e7
Create Date: 2026-09-11 23:44:43.829179

Nine tables carry a nullable `deleted_at` used for soft-delete filtering
(`deleted_at IS NULL` for "still active") — found via a direct
information_schema/pg_constraint sweep against the live schema, cross-
checked against 136 call sites across 13 service modules that actually
filter on it. None of them had a covering index; the largest,
archive_files, has 27900+ rows and grows with every archive upload.

A partial index (`WHERE deleted_at IS NULL`) rather than a plain one:
almost every row is currently active (e.g. 27255 of 27916 on
archive_files), so it stays close to full-table size either way, but a
partial index keeps future soft-deleted rows out of it and matches the
project's own established pattern for this predicate (see
20db5b81733c_p4x_category_directs_active_unique_index.py and
2de017d723c6_standesdb_images_id_uuid_and_fk_cutover.py, both already
using postgresql_where=sa.text("deleted_at IS NULL") for unique
constraints).

CREATE INDEX CONCURRENTLY (not a plain CREATE INDEX) to avoid taking an
exclusive lock on tables that see live traffic — each needs its own
autocommit block since CONCURRENTLY cannot run inside a transaction
(same approach as fdd134a00dd3_index_uncovered_fk_columns.py).
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1fbacf33990"
down_revision: str | None = "40cd764a92e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOFT_DELETE_TABLES: list[str] = [
    "archive_files",
    "p4x_transactions",
    "p4x_category_directs",
    "archive_dirs",
    "p4x_partners",
    "standesdb_images",
    "contacts",
    "p4x_accounts",
    "archive_file_comments",
]


def upgrade() -> None:
    """Upgrade schema."""
    with op.get_context().autocommit_block():
        for table in _SOFT_DELETE_TABLES:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_{table}_deleted_at "
                f"ON {table} (deleted_at) WHERE deleted_at IS NULL"
            )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        for table in reversed(_SOFT_DELETE_TABLES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS ix_{table}_deleted_at")
