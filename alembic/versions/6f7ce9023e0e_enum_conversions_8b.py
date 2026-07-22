"""enum conversions 8b

Revision ID: 6f7ce9023e0e
Revises: 825f28b2896d
Create Date: 2026-07-22 23:35:20.796813

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6f7ce9023e0e"
down_revision: str | Sequence[str] | None = "825f28b2896d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

contact_type_enum = postgresql.ENUM(
    "person", "organisation", name="contact_type", create_type=False
)
badge_group_enum = postgresql.ENUM(
    "jubelband", "ehrenzeichen", name="badge_group", create_type=False
)
role_group_enum = postgresql.ENUM(
    "philchc",
    "funktion",
    "verbindungsgericht",
    "kommission",
    "chc",
    name="role_group",
    create_type=False,
)
subject_mode_enum = postgresql.ENUM(
    "contains", "equals", "starts", name="p4x_filter_subject_mode", create_type=False
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    contact_type_enum.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "contacts",
        "kontakttyp",
        existing_type=sa.String(),
        type_=contact_type_enum,
        existing_nullable=False,
        postgresql_using="kontakttyp::contact_type",
    )

    badge_group_enum.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "badges",
        "group",
        existing_type=sa.String(),
        type_=badge_group_enum,
        existing_nullable=True,
        postgresql_using='"group"::badge_group',
    )

    role_group_enum.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "roles",
        "group",
        existing_type=sa.String(),
        type_=role_group_enum,
        existing_nullable=True,
        postgresql_using='"group"::role_group',
    )

    subject_mode_enum.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "p4x_category_filters",
        "subject_mode",
        existing_type=sa.String(),
        type_=subject_mode_enum,
        existing_nullable=False,
        postgresql_using="subject_mode::p4x_filter_subject_mode",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.alter_column(
        "p4x_category_filters",
        "subject_mode",
        existing_type=subject_mode_enum,
        type_=sa.String(),
        existing_nullable=False,
        postgresql_using="subject_mode::text",
    )
    subject_mode_enum.drop(op.get_bind(), checkfirst=True)

    op.alter_column(
        "roles",
        "group",
        existing_type=role_group_enum,
        type_=sa.String(),
        existing_nullable=True,
        postgresql_using='"group"::text',
    )
    role_group_enum.drop(op.get_bind(), checkfirst=True)

    op.alter_column(
        "badges",
        "group",
        existing_type=badge_group_enum,
        type_=sa.String(),
        existing_nullable=True,
        postgresql_using='"group"::text',
    )
    badge_group_enum.drop(op.get_bind(), checkfirst=True)

    op.alter_column(
        "contacts",
        "kontakttyp",
        existing_type=contact_type_enum,
        type_=sa.String(),
        existing_nullable=False,
        postgresql_using="kontakttyp::text",
    )
    contact_type_enum.drop(op.get_bind(), checkfirst=True)
