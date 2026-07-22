"""standesdb images exclusive arc

Revision ID: 1e14a4e8ec0c
Revises: 5292367fb696
Create Date: 2026-07-22 15:06:51.003450

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1e14a4e8ec0c"
down_revision: str | Sequence[str] | None = "5292367fb696"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # owner_type/owner_id was a polymorphic pair with no real FK (a plain FK
    # can't point at "whichever table a discriminator column names").
    # Replaced with an exclusive-arc pattern: one nullable FK per real
    # target (members, contacts), a CHECK enforcing exactly one is set.
    op.add_column(
        "standesdb_images", sa.Column("owner_member_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "standesdb_images", sa.Column("owner_contact_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "standesdb_images_owner_member_id_fkey",
        "standesdb_images",
        "members",
        ["owner_member_id"],
        ["id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        "standesdb_images_owner_contact_id_fkey",
        "standesdb_images",
        "contacts",
        ["owner_contact_id"],
        ["id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )

    op.execute(
        "UPDATE standesdb_images SET owner_member_id = owner_id "
        "WHERE owner_type = 'member'"
    )
    op.execute(
        "UPDATE standesdb_images SET owner_contact_id = owner_id "
        "WHERE owner_type = 'contact'"
    )

    op.create_check_constraint(
        "standesdb_images_owner_exclusive_arc_check",
        "standesdb_images",
        "(owner_member_id IS NOT NULL AND owner_contact_id IS NULL) "
        "OR (owner_member_id IS NULL AND owner_contact_id IS NOT NULL)",
    )

    op.drop_column("standesdb_images", "owner_type")
    op.drop_column("standesdb_images", "owner_id")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.add_column(
        "standesdb_images", sa.Column("owner_type", sa.String(), nullable=True)
    )
    op.add_column(
        "standesdb_images", sa.Column("owner_id", sa.Integer(), nullable=True)
    )

    op.execute(
        "UPDATE standesdb_images SET owner_type = 'member', "
        "owner_id = owner_member_id WHERE owner_member_id IS NOT NULL"
    )
    op.execute(
        "UPDATE standesdb_images SET owner_type = 'contact', "
        "owner_id = owner_contact_id WHERE owner_contact_id IS NOT NULL"
    )

    op.alter_column("standesdb_images", "owner_type", nullable=False)
    op.alter_column("standesdb_images", "owner_id", nullable=False)

    op.drop_constraint(
        "standesdb_images_owner_exclusive_arc_check",
        "standesdb_images",
        type_="check",
    )
    op.drop_constraint(
        "standesdb_images_owner_member_id_fkey",
        "standesdb_images",
        type_="foreignkey",
    )
    op.drop_constraint(
        "standesdb_images_owner_contact_id_fkey",
        "standesdb_images",
        type_="foreignkey",
    )
    op.drop_column("standesdb_images", "owner_member_id")
    op.drop_column("standesdb_images", "owner_contact_id")
