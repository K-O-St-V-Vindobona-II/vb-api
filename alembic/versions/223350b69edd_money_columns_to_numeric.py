"""money columns to numeric

Revision ID: 223350b69edd
Revises: cb250c054945
Create Date: 2026-07-22 13:06:22.296962

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "223350b69edd"
down_revision: str | Sequence[str] | None = "cb250c054945"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.alter_column(
        "p4x_transactions",
        "amount",
        existing_type=sa.Float(),
        type_=sa.Numeric(12, 2),
        existing_nullable=False,
        postgresql_using="ROUND(amount::numeric, 2)",
    )
    op.alter_column(
        "p4x_category_directs",
        "amount",
        existing_type=sa.Float(),
        type_=sa.Numeric(12, 2),
        existing_nullable=False,
        postgresql_using="ROUND(amount::numeric, 2)",
    )
    op.alter_column(
        "p4x_accounts",
        "init_balance",
        existing_type=sa.Float(),
        type_=sa.Numeric(12, 2),
        existing_nullable=False,
        postgresql_using="ROUND(init_balance::numeric, 2)",
    )
    op.alter_column(
        "p4x_fees",
        "fee",
        existing_type=sa.Float(),
        type_=sa.Numeric(12, 2),
        existing_nullable=False,
        postgresql_using="ROUND(fee::numeric, 2)",
    )
    op.alter_column(
        "p4x_category_filters",
        "min_amount",
        existing_type=sa.Float(),
        type_=sa.Numeric(12, 2),
        existing_nullable=True,
        postgresql_using="ROUND(min_amount::numeric, 2)",
    )
    op.alter_column(
        "p4x_category_filters",
        "max_amount",
        existing_type=sa.Float(),
        type_=sa.Numeric(12, 2),
        existing_nullable=True,
        postgresql_using="ROUND(max_amount::numeric, 2)",
    )
    op.alter_column(
        "members",
        "p4x_init_balance",
        existing_type=sa.Integer(),
        type_=sa.Numeric(12, 2),
        existing_nullable=True,
        postgresql_using="p4x_init_balance::numeric(12, 2)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.alter_column(
        "members",
        "p4x_init_balance",
        existing_type=sa.Numeric(12, 2),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="ROUND(p4x_init_balance)::integer",
    )
    op.alter_column(
        "p4x_category_filters",
        "max_amount",
        existing_type=sa.Numeric(12, 2),
        type_=sa.Float(),
        existing_nullable=True,
        postgresql_using="max_amount::float8",
    )
    op.alter_column(
        "p4x_category_filters",
        "min_amount",
        existing_type=sa.Numeric(12, 2),
        type_=sa.Float(),
        existing_nullable=True,
        postgresql_using="min_amount::float8",
    )
    op.alter_column(
        "p4x_fees",
        "fee",
        existing_type=sa.Numeric(12, 2),
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using="fee::float8",
    )
    op.alter_column(
        "p4x_accounts",
        "init_balance",
        existing_type=sa.Numeric(12, 2),
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using="init_balance::float8",
    )
    op.alter_column(
        "p4x_category_directs",
        "amount",
        existing_type=sa.Numeric(12, 2),
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using="amount::float8",
    )
    op.alter_column(
        "p4x_transactions",
        "amount",
        existing_type=sa.Numeric(12, 2),
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using="amount::float8",
    )
