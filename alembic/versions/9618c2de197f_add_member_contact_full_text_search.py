"""add member/contact full text search

Revision ID: 9618c2de197f
Revises: ed7a7b858a9f
Create Date: 2026-08-08 21:10:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9618c2de197f"
down_revision: str | Sequence[str] | None = "ed7a7b858a9f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Weight story: A = the name itself (vorname+nachname / name), B = the
# couleurname (a secondary, often-used identifier), C = org_id - included
# so a query can mix a name term with an org qualifier in one search
# string (e.g. "schimpl vbn" must find only the vbn member of that name,
# not every "Schimpl" across orgs) without needing a separate filter
# parameter. coalesce() everywhere: to_tsvector(NULL) is NULL, and
# NULL || anything is NULL too - without it, one NULL column (couleurname/
# org_id are both nullable) would blank out the whole combined vector,
# including the name match.
_MEMBERS_EXPR = (
    "setweight(to_tsvector('german', coalesce(vorname, '') || ' ' || "
    "coalesce(nachname, '')), 'A') || "
    "setweight(to_tsvector('german', coalesce(couleurname, '')), 'B') || "
    "setweight(to_tsvector('german', coalesce(org_id, '')), 'C')"
)
_CONTACTS_EXPR = (
    "setweight(to_tsvector('german', coalesce(name, '')), 'A') || "
    "setweight(to_tsvector('german', coalesce(couleurname, '')), 'B') || "
    "setweight(to_tsvector('german', coalesce(org_id, '')), 'C')"
)

_TABLES = (
    ("members", _MEMBERS_EXPR),
    ("contacts", _CONTACTS_EXPR),
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
