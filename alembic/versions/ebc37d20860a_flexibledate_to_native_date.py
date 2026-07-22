"""flexibledate to native date

Revision ID: ebc37d20860a
Revises: 514ac66eb66e
Create Date: 2026-07-22 22:38:25.187095

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ebc37d20860a"
down_revision: str | Sequence[str] | None = "514ac66eb66e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, nullable) for every column previously typed as the
# now-deleted app.models.types.FlexibleDate (a TypeDecorator whose DB-level
# impl was plain String/VARCHAR, kept only to parse the legacy SQLite text
# formats '2006-01-09' and '2006-01-09 00:00:00'). Switched to a native
# Postgres DATE column — a pure storage-type tightening, unrelated to the
# separate *_accuracy "fuzzy date" columns (year/month/day precision),
# which are untouched by this migration.
_FLEXIBLE_DATE_COLUMNS: list[tuple[str, str, bool]] = [
    ("members", "geburtsdatum", True),
    ("members", "aufnahmedatum", True),
    ("members", "branderdatum", True),
    ("members", "burschungsdatum", True),
    ("members", "philistrierungsdatum", True),
    ("members", "entlassungsdatum", True),
    ("members", "sterbedatum", True),
    ("members", "p4x_init_date", True),
    ("contacts", "datum", True),
    ("members_roles", "startdate", False),
    ("members_roles", "enddate", True),
    ("members_badges", "presentationdate", True),
    ("members_keys", "presentationdate", True),
    ("p4x_accounts", "init_date", True),
    ("p4x_fees", "start", False),
    ("p4x_summary_orders", "summary_start", False),
    ("p4x_summary_orders", "summary_end", False),
    ("p4x_transactions", "booking", False),
    ("p4x_transactions", "valuation", False),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    for table, column, nullable in _FLEXIBLE_DATE_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.String(),
            type_=sa.Date(),
            existing_nullable=nullable,
            postgresql_using=f"split_part({column}, ' ', 1)::date",
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    for table, column, nullable in _FLEXIBLE_DATE_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Date(),
            type_=sa.String(),
            existing_nullable=nullable,
            postgresql_using=f"{column}::text",
        )
