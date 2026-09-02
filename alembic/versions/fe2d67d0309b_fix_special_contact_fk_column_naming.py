"""fix special_contact fk column naming

Revision ID: fe2d67d0309b
Revises: 4ec3e7fa9cfd
Create Date: 2026-09-02 22:00:00.000000

p4x_special_contacts is the target table (correctly snake_case, matches
its own P4xSpecialcontact model's __tablename__), but both FK columns
pointing at it were named without the second underscore -
p4x_partners.p4x_specialcontact_id and p4x_transactions.delegating_
p4x_specialcontact_id - violating the project's `[singular_target_
table_name]_id` naming convention. p4x_partners' own FK constraint was
already correctly named p4x_partners_p4x_special_contact_id_fkey at
creation time despite the column name being wrong, confirming the
underscore was always the intended spelling.

Column RENAME automatically carries FK targets, index definitions and
CHECK constraint expressions along (Postgres tracks all three by
attnum, not by name) - only the column name and the two object names
that are pure text labels (an index name, and p4x_transactions' FK
constraint name, which - unlike p4x_partners' - was also misspelled)
need explicit renaming.
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fe2d67d0309b"
down_revision: str | None = "4ec3e7fa9cfd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "p4x_partners",
        "p4x_specialcontact_id",
        new_column_name="p4x_special_contact_id",
    )
    op.execute(
        "ALTER INDEX ix_p4x_partners_p4x_specialcontact_id "
        "RENAME TO ix_p4x_partners_p4x_special_contact_id"
    )

    op.alter_column(
        "p4x_transactions",
        "delegating_p4x_specialcontact_id",
        new_column_name="delegating_p4x_special_contact_id",
    )
    op.execute(
        "ALTER INDEX ix_p4x_transactions_delegating_p4x_specialcontact_id "
        "RENAME TO ix_p4x_transactions_delegating_p4x_special_contact_id"
    )
    op.execute(
        "ALTER TABLE p4x_transactions "
        "RENAME CONSTRAINT p4x_transactions_delegating_p4x_specialcontact_id_fkey "
        "TO p4x_transactions_delegating_p4x_special_contact_id_fkey"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER TABLE p4x_transactions "
        "RENAME CONSTRAINT p4x_transactions_delegating_p4x_special_contact_id_fkey "
        "TO p4x_transactions_delegating_p4x_specialcontact_id_fkey"
    )
    op.execute(
        "ALTER INDEX ix_p4x_transactions_delegating_p4x_special_contact_id "
        "RENAME TO ix_p4x_transactions_delegating_p4x_specialcontact_id"
    )
    op.alter_column(
        "p4x_transactions",
        "delegating_p4x_special_contact_id",
        new_column_name="delegating_p4x_specialcontact_id",
    )

    op.execute(
        "ALTER INDEX ix_p4x_partners_p4x_special_contact_id "
        "RENAME TO ix_p4x_partners_p4x_specialcontact_id"
    )
    op.alter_column(
        "p4x_partners",
        "p4x_special_contact_id",
        new_column_name="p4x_specialcontact_id",
    )
