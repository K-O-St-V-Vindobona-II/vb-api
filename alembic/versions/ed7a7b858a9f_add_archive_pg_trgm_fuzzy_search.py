"""add archive pg_trgm fuzzy search

Revision ID: ed7a7b858a9f
Revises: 10efdd07c37e
Create Date: 2026-08-08 20:32:02.670159

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ed7a7b858a9f"
down_revision: str | Sequence[str] | None = "10efdd07c37e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same four tables/columns as the tsvector search (migration 10efdd07c37e),
# but on the raw text columns directly - pg_trgm's word_similarity() needs
# the original text, not a preprocessed tsvector, to find a typo'd
# substring inside a longer field.
_TRGM_COLUMNS = (
    ("archive_dirs", "name"),
    ("archive_dirs", "description"),
    ("archive_store_items", "name"),
    ("archive_store_items", "extension"),
    ("archive_files", "description"),
    ("archive_file_comments", "content"),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    for table, column in _TRGM_COLUMNS:
        op.execute(
            f"CREATE INDEX ix_{table}_{column}_trgm "
            f"ON {table} USING gin ({column} gin_trgm_ops)"
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    for table, column in _TRGM_COLUMNS:
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_{column}_trgm")

    # No other feature in this schema uses pg_trgm - safe to drop.
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
