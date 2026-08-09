"""add member/contact pg_trgm fuzzy search

Revision ID: 46e5c8d03b55
Revises: 9618c2de197f
Create Date: 2026-08-08 21:15:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "46e5c8d03b55"
down_revision: str | Sequence[str] | None = "9618c2de197f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Deliberately name-only, no org_id: org codes ("vbw"/"vbn") differ by a
# single character, so fuzzy-matching them via trigram similarity risks
# defeating the exact reason org_id is part of the Stage 1 tsvector in the
# first place (a typo'd org qualifier could fuzzy-match the *other* org).
# The fuzzy fallback stays scoped to genuine name typos.
_TRGM_COLUMNS = (
    ("members", "vorname"),
    ("members", "nachname"),
    ("members", "couleurname"),
    ("contacts", "name"),
    ("contacts", "couleurname"),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # pg_trgm was already created by migration ed7a7b858a9f (archive search)
    # - IF NOT EXISTS makes this migration safe to run standalone too (e.g.
    # a future downgrade/upgrade cycle that only touches this revision).
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

    # Not dropping the pg_trgm extension here - migration ed7a7b858a9f
    # (archive search) still depends on it and owns the drop.
