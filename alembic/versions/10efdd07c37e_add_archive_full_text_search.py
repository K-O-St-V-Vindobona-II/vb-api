"""add archive full text search

Revision ID: 10efdd07c37e
Revises: 757b97f0fae4
Create Date: 2026-08-08 20:00:36.613251

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "10efdd07c37e"
down_revision: str | Sequence[str] | None = "757b97f0fae4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Weight story, consistent across all four tables: A = name (most central to
# what the item "is"), B = free-text description, C = extension (a short,
# exact-match technical detail, not real "content"), D = comment content
# (least central - a comment mentioning something isn't the same as the
# item being about it). coalesce() everywhere: to_tsvector(NULL) is NULL,
# and NULL || anything is NULL too - without it, one NULL column would
# blank out the whole combined vector, including the name match.
_ARCHIVE_DIRS_EXPR = (
    "setweight(to_tsvector('german', coalesce(name, '')), 'A') || "
    "setweight(to_tsvector('german', coalesce(description, '')), 'B')"
)
_ARCHIVE_STORE_ITEMS_EXPR = (
    "setweight(to_tsvector('german', coalesce(name, '')), 'A') || "
    "setweight(to_tsvector('german', coalesce(extension, '')), 'C')"
)
_ARCHIVE_FILES_EXPR = "setweight(to_tsvector('german', coalesce(description, '')), 'B')"
_ARCHIVE_FILE_COMMENTS_EXPR = (
    "setweight(to_tsvector('german', coalesce(content, '')), 'D')"
)

_TABLES = (
    ("archive_dirs", _ARCHIVE_DIRS_EXPR),
    ("archive_store_items", _ARCHIVE_STORE_ITEMS_EXPR),
    ("archive_files", _ARCHIVE_FILES_EXPR),
    ("archive_file_comments", _ARCHIVE_FILE_COMMENTS_EXPR),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    for table, expr in _TABLES:
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN search_vector tsvector "
            f"GENERATED ALWAYS AS ({expr}) STORED"
        )
        op.execute(
            f"CREATE INDEX ix_{table}_search_vector "
            f"ON {table} USING gin (search_vector)"
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    for table, _ in _TABLES:
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_search_vector")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS search_vector")
