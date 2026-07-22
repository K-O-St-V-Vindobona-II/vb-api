"""enum conversions 8c

Revision ID: 42df0f369b0b
Revises: 6f7ce9023e0e
Create Date: 2026-07-23 00:07:24.351392

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "42df0f369b0b"
down_revision: str | Sequence[str] | None = "6f7ce9023e0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

delivery_preference_enum = postgresql.ENUM(
    "deaktiviert",
    "adresse_privat",
    "adresse_beruf",
    name="member_delivery_preference",
    create_type=False,
)
changelog_action_enum = postgresql.ENUM(
    "create", "update", "delete", name="changelog_action", create_type=False
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    delivery_preference_enum.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "members",
        "zustellungen",
        existing_type=sa.String(),
        type_=delivery_preference_enum,
        existing_nullable=True,
        postgresql_using="zustellungen::member_delivery_preference",
    )

    changelog_action_enum.create(op.get_bind(), checkfirst=True)
    # Historical data predates the "store" -> "create" code rename (see
    # app/services/standesdb_service.py) and already only ever contains
    # 'create'/'update' — no backfill needed, this is a lossless cast.
    op.alter_column(
        "members_logs",
        "action",
        existing_type=sa.String(),
        type_=changelog_action_enum,
        existing_nullable=False,
        postgresql_using="action::changelog_action",
    )
    op.alter_column(
        "contacts_logs",
        "action",
        existing_type=sa.String(),
        type_=changelog_action_enum,
        existing_nullable=False,
        postgresql_using="action::changelog_action",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.alter_column(
        "contacts_logs",
        "action",
        existing_type=changelog_action_enum,
        type_=sa.String(),
        existing_nullable=False,
        postgresql_using="action::text",
    )
    op.alter_column(
        "members_logs",
        "action",
        existing_type=changelog_action_enum,
        type_=sa.String(),
        existing_nullable=False,
        postgresql_using="action::text",
    )
    changelog_action_enum.drop(op.get_bind(), checkfirst=True)

    op.alter_column(
        "members",
        "zustellungen",
        existing_type=delivery_preference_enum,
        type_=sa.String(),
        existing_nullable=True,
        postgresql_using="zustellungen::text",
    )
    delivery_preference_enum.drop(op.get_bind(), checkfirst=True)
