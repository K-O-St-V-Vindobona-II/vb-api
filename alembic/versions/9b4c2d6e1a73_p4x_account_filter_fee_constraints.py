"""p4x: unique active account IBAN, filter criterion, fees start on the 1st

Revision ID: 9b4c2d6e1a73
Revises: b7e4a2c91d35
Create Date: 2026-10-08 12:00:00.000000

Three rules the application applies and the database did not:

1. p4x_accounts.iban was UNIQUE over all rows, deleted accounts included and
   spelling-sensitive: the IBAN of a deleted account could never be added
   again (a database error) while "AT61 1904 ..." and "AT611904 ..." could
   both be stored. It is now unique without blanks among accounts that are not
   deleted. The migration stops with the list if two active accounts clash.
2. A category filter without any criterion (no IBAN, no amount limit, no
   subject) matches every transaction. A CHECK now requires one. The migration
   stops with the names of filters that have none.
3. A fee applies from the first day of a month; the single-member balance
   looks fees up by the first of each month while the bulk list rounds a
   start date down, so a fee starting mid-month was counted differently in the
   two. A CHECK requires the first day. The migration stops with the list of
   fees that do not start on the 1st.

The downgrade restores the plain unique constraint on the IBAN and stops with
the list if an IBAN is now used by more than one row (a deleted account whose
IBAN was added again).
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b4c2d6e1a73"
down_revision: str | Sequence[str] | None = "b7e4a2c91d35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FILTER_CRITERION = (
    "coalesce(iban, '') <> '' OR min_amount IS NOT NULL"
    " OR max_amount IS NOT NULL OR coalesce(subject, '') <> ''"
)


def _refuse_if_any(rows: Sequence[object], what: str) -> None:
    if rows:
        msg = f"{what}: {list(rows)}"
        raise RuntimeError(msg)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    bind = op.get_bind()

    _refuse_if_any(
        bind.execute(
            sa.text(
                "SELECT replace(iban, ' ', '') FROM p4x_accounts "
                "WHERE deleted_at IS NULL "
                "GROUP BY replace(iban, ' ', '') HAVING count(*) > 1"
            )
        )
        .scalars()
        .all(),
        "Active accounts with the same IBAN",
    )
    _refuse_if_any(
        bind.execute(
            sa.text(
                f"SELECT name FROM p4x_category_filters WHERE NOT ({_FILTER_CRITERION})"  # noqa: S608
            )
        )
        .scalars()
        .all(),
        "Category filters without any criterion",
    )
    _refuse_if_any(
        bind.execute(
            sa.text(
                "SELECT start::text FROM p4x_fees WHERE extract(day FROM start) <> 1"
            )
        )
        .scalars()
        .all(),
        "Fees that do not start on the first of a month",
    )

    op.drop_constraint("p4x_accounts_iban_key", "p4x_accounts", type_="unique")
    op.create_index(
        "p4x_accounts_iban_active_key",
        "p4x_accounts",
        [sa.text("replace(iban, ' ', '')")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_check_constraint(
        "p4x_category_filters_criterion_check",
        "p4x_category_filters",
        _FILTER_CRITERION,
    )
    op.create_check_constraint(
        "p4x_fees_start_first_of_month_check",
        "p4x_fees",
        "extract(day FROM start) = 1",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    _refuse_if_any(
        op.get_bind()
        .execute(
            sa.text("SELECT iban FROM p4x_accounts GROUP BY iban HAVING count(*) > 1")
        )
        .scalars()
        .all(),
        "IBANs used by more than one account",
    )

    op.drop_constraint("p4x_fees_start_first_of_month_check", "p4x_fees", type_="check")
    op.drop_constraint(
        "p4x_category_filters_criterion_check", "p4x_category_filters", type_="check"
    )
    op.drop_index("p4x_accounts_iban_active_key", table_name="p4x_accounts")
    op.create_unique_constraint("p4x_accounts_iban_key", "p4x_accounts", ["iban"])
