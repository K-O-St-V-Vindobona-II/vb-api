"""personal access tokens real fk

Revision ID: 5292367fb696
Revises: 223350b69edd
Create Date: 2026-07-22 14:19:20.991970

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5292367fb696"
down_revision: str | Sequence[str] | None = "223350b69edd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # tokenable_type/tokenable_id was vestigial polymorphism: the only value
    # ever written was "Member", and no code path branches on it — every
    # call site already treats tokenable_id as a plain Member.id. Replaced
    # with a real FK, renamed to match the naming convention for FK columns.
    op.alter_column(
        "personal_access_tokens", "tokenable_id", new_column_name="member_id"
    )
    op.drop_column("personal_access_tokens", "tokenable_type")
    op.create_foreign_key(
        "personal_access_tokens_member_id_fkey",
        "personal_access_tokens",
        "members",
        ["member_id"],
        ["id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "personal_access_tokens_member_id_fkey",
        "personal_access_tokens",
        type_="foreignkey",
    )
    op.add_column(
        "personal_access_tokens",
        sa.Column(
            "tokenable_type", sa.String(), nullable=False, server_default="Member"
        ),
    )
    op.alter_column(
        "personal_access_tokens", "member_id", new_column_name="tokenable_id"
    )
