"""add p4x fee interval

Revision ID: 5a5304ed2b43
Revises: 757b97f0fae4
Create Date: 2026-08-07 21:20:15.634350

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5a5304ed2b43"
down_revision: str | Sequence[str] | None = "757b97f0fae4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# How often a fee member normally pays their dues - governs the
# advance-payment ceiling introduced in p4x_fee_balance_service.py (see that
# module for the full FEE_INTERVAL_CEILING_CUTOVER rationale). 'unlimited'
# is a deliberate, always-manually-set trust decision and must never be
# assigned by automation.
p4x_fee_interval_enum = postgresql.ENUM(
    "monthly",
    "quarterly",
    "semiannual",
    "annual",
    "unlimited",
    name="p4x_fee_interval",
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    p4x_fee_interval_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "members",
        sa.Column(
            "p4x_fee_interval",
            p4x_fee_interval_enum,
            nullable=False,
            server_default="monthly",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.drop_column("members", "p4x_fee_interval")
    p4x_fee_interval_enum.drop(op.get_bind(), checkfirst=True)
