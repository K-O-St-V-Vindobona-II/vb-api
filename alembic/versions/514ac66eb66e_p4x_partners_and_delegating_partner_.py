"""p4x partners and delegating partner exclusive arc

Revision ID: 514ac66eb66e
Revises: 1e14a4e8ec0c
Create Date: 2026-07-22 21:50:48.354960

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "514ac66eb66e"
down_revision: str | Sequence[str] | None = "1e14a4e8ec0c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # p4x_partners.partner_type/partner_id was a polymorphic pair with no
    # real FK (a plain FK can't point at "whichever table a discriminator
    # column names"). Replaced with an exclusive-arc pattern: one nullable
    # FK per real target (members, contacts, p4x_accounts,
    # p4x_specialcontacts), a CHECK enforcing exactly one is set (the old
    # columns were NOT NULL, so a partner is always required).
    op.add_column("p4x_partners", sa.Column("member_id", sa.Integer(), nullable=True))
    op.add_column("p4x_partners", sa.Column("contact_id", sa.Integer(), nullable=True))
    op.add_column(
        "p4x_partners", sa.Column("p4x_account_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "p4x_partners",
        sa.Column("p4x_specialcontact_id", sa.Integer(), nullable=True),
    )

    op.create_foreign_key(
        "p4x_partners_member_id_fkey",
        "p4x_partners",
        "members",
        ["member_id"],
        ["id"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        "p4x_partners_contact_id_fkey",
        "p4x_partners",
        "contacts",
        ["contact_id"],
        ["id"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        "p4x_partners_p4x_account_id_fkey",
        "p4x_partners",
        "p4x_accounts",
        ["p4x_account_id"],
        ["id"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        "p4x_partners_p4x_specialcontact_id_fkey",
        "p4x_partners",
        "p4x_specialcontacts",
        ["p4x_specialcontact_id"],
        ["id"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )

    op.execute(
        "UPDATE p4x_partners SET member_id = partner_id WHERE partner_type = 'member'"
    )
    op.execute(
        "UPDATE p4x_partners SET contact_id = partner_id WHERE partner_type = 'contact'"
    )
    op.execute(
        "UPDATE p4x_partners SET p4x_account_id = partner_id"
        " WHERE partner_type = 'account'"
    )
    op.execute(
        "UPDATE p4x_partners SET p4x_specialcontact_id = partner_id"
        " WHERE partner_type = 'special'"
    )

    op.create_check_constraint(
        "p4x_partners_partner_exclusive_arc_check",
        "p4x_partners",
        "num_nonnulls(member_id, contact_id, p4x_account_id,"
        " p4x_specialcontact_id) = 1",
    )

    op.drop_index(op.f("ix_p4x_partners_partner_id"), table_name="p4x_partners")
    op.drop_column("p4x_partners", "partner_type")
    op.drop_column("p4x_partners", "partner_id")

    op.create_index(op.f("ix_p4x_partners_member_id"), "p4x_partners", ["member_id"])
    op.create_index(op.f("ix_p4x_partners_contact_id"), "p4x_partners", ["contact_id"])
    op.create_index(
        op.f("ix_p4x_partners_p4x_account_id"),
        "p4x_partners",
        ["p4x_account_id"],
    )
    op.create_index(
        op.f("ix_p4x_partners_p4x_specialcontact_id"),
        "p4x_partners",
        ["p4x_specialcontact_id"],
    )

    # p4x_transactions.delegating_partner_type/delegating_partner_id: same
    # antipattern, same fix. Unlike p4x_partners this pair is optional
    # (nullable today), so the CHECK only enforces "at most one" instead of
    # "exactly one" — all four columns NULL remains a valid "no delegation"
    # state.
    op.add_column(
        "p4x_transactions",
        sa.Column("delegating_member_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "p4x_transactions",
        sa.Column("delegating_contact_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "p4x_transactions",
        sa.Column("delegating_p4x_account_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "p4x_transactions",
        sa.Column("delegating_p4x_specialcontact_id", sa.Integer(), nullable=True),
    )

    op.create_foreign_key(
        "p4x_transactions_delegating_member_id_fkey",
        "p4x_transactions",
        "members",
        ["delegating_member_id"],
        ["id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        "p4x_transactions_delegating_contact_id_fkey",
        "p4x_transactions",
        "contacts",
        ["delegating_contact_id"],
        ["id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        "p4x_transactions_delegating_p4x_account_id_fkey",
        "p4x_transactions",
        "p4x_accounts",
        ["delegating_p4x_account_id"],
        ["id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        "p4x_transactions_delegating_p4x_specialcontact_id_fkey",
        "p4x_transactions",
        "p4x_specialcontacts",
        ["delegating_p4x_specialcontact_id"],
        ["id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )

    op.execute(
        "UPDATE p4x_transactions SET delegating_member_id = delegating_partner_id"
        " WHERE delegating_partner_type = 'member'"
    )
    op.execute(
        "UPDATE p4x_transactions SET delegating_contact_id = delegating_partner_id"
        " WHERE delegating_partner_type = 'contact'"
    )
    op.execute(
        "UPDATE p4x_transactions SET delegating_p4x_account_id ="
        " delegating_partner_id WHERE delegating_partner_type = 'account'"
    )
    op.execute(
        "UPDATE p4x_transactions SET delegating_p4x_specialcontact_id ="
        " delegating_partner_id WHERE delegating_partner_type = 'special'"
    )

    op.create_check_constraint(
        "p4x_transactions_delegating_partner_arc_check",
        "p4x_transactions",
        "num_nonnulls(delegating_member_id, delegating_contact_id,"
        " delegating_p4x_account_id, delegating_p4x_specialcontact_id) <= 1",
    )

    op.drop_index(
        op.f("ix_p4x_transactions_delegating_partner_id"),
        table_name="p4x_transactions",
    )
    op.drop_index(
        op.f("ix_p4x_transactions_delegating_partner_type"),
        table_name="p4x_transactions",
    )
    op.drop_column("p4x_transactions", "delegating_partner_type")
    op.drop_column("p4x_transactions", "delegating_partner_id")

    op.create_index(
        op.f("ix_p4x_transactions_delegating_member_id"),
        "p4x_transactions",
        ["delegating_member_id"],
    )
    op.create_index(
        op.f("ix_p4x_transactions_delegating_contact_id"),
        "p4x_transactions",
        ["delegating_contact_id"],
    )
    op.create_index(
        op.f("ix_p4x_transactions_delegating_p4x_account_id"),
        "p4x_transactions",
        ["delegating_p4x_account_id"],
    )
    op.create_index(
        op.f("ix_p4x_transactions_delegating_p4x_specialcontact_id"),
        "p4x_transactions",
        ["delegating_p4x_specialcontact_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- p4x_transactions: revert to delegating_partner_type/partner_id ---
    op.add_column(
        "p4x_transactions",
        sa.Column("delegating_partner_type", sa.String(), nullable=True),
    )
    op.add_column(
        "p4x_transactions",
        sa.Column("delegating_partner_id", sa.Integer(), nullable=True),
    )

    op.execute(
        "UPDATE p4x_transactions SET delegating_partner_type = 'member',"
        " delegating_partner_id = delegating_member_id"
        " WHERE delegating_member_id IS NOT NULL"
    )
    op.execute(
        "UPDATE p4x_transactions SET delegating_partner_type = 'contact',"
        " delegating_partner_id = delegating_contact_id"
        " WHERE delegating_contact_id IS NOT NULL"
    )
    op.execute(
        "UPDATE p4x_transactions SET delegating_partner_type = 'account',"
        " delegating_partner_id = delegating_p4x_account_id"
        " WHERE delegating_p4x_account_id IS NOT NULL"
    )
    op.execute(
        "UPDATE p4x_transactions SET delegating_partner_type = 'special',"
        " delegating_partner_id = delegating_p4x_specialcontact_id"
        " WHERE delegating_p4x_specialcontact_id IS NOT NULL"
    )

    op.drop_constraint(
        "p4x_transactions_delegating_partner_arc_check",
        "p4x_transactions",
        type_="check",
    )
    op.drop_constraint(
        "p4x_transactions_delegating_member_id_fkey",
        "p4x_transactions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "p4x_transactions_delegating_contact_id_fkey",
        "p4x_transactions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "p4x_transactions_delegating_p4x_account_id_fkey",
        "p4x_transactions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "p4x_transactions_delegating_p4x_specialcontact_id_fkey",
        "p4x_transactions",
        type_="foreignkey",
    )
    op.drop_column("p4x_transactions", "delegating_member_id")
    op.drop_column("p4x_transactions", "delegating_contact_id")
    op.drop_column("p4x_transactions", "delegating_p4x_account_id")
    op.drop_column("p4x_transactions", "delegating_p4x_specialcontact_id")

    op.create_index(
        op.f("ix_p4x_transactions_delegating_partner_id"),
        "p4x_transactions",
        ["delegating_partner_id"],
    )
    op.create_index(
        op.f("ix_p4x_transactions_delegating_partner_type"),
        "p4x_transactions",
        ["delegating_partner_type"],
    )

    # --- p4x_partners: revert to partner_type/partner_id ---
    op.add_column("p4x_partners", sa.Column("partner_type", sa.String(), nullable=True))
    op.add_column("p4x_partners", sa.Column("partner_id", sa.Integer(), nullable=True))

    op.execute(
        "UPDATE p4x_partners SET partner_type = 'member',"
        " partner_id = member_id WHERE member_id IS NOT NULL"
    )
    op.execute(
        "UPDATE p4x_partners SET partner_type = 'contact',"
        " partner_id = contact_id WHERE contact_id IS NOT NULL"
    )
    op.execute(
        "UPDATE p4x_partners SET partner_type = 'account',"
        " partner_id = p4x_account_id WHERE p4x_account_id IS NOT NULL"
    )
    op.execute(
        "UPDATE p4x_partners SET partner_type = 'special',"
        " partner_id = p4x_specialcontact_id"
        " WHERE p4x_specialcontact_id IS NOT NULL"
    )

    op.alter_column("p4x_partners", "partner_type", nullable=False)
    op.alter_column("p4x_partners", "partner_id", nullable=False)

    op.drop_constraint(
        "p4x_partners_partner_exclusive_arc_check",
        "p4x_partners",
        type_="check",
    )
    op.drop_constraint(
        "p4x_partners_member_id_fkey", "p4x_partners", type_="foreignkey"
    )
    op.drop_constraint(
        "p4x_partners_contact_id_fkey", "p4x_partners", type_="foreignkey"
    )
    op.drop_constraint(
        "p4x_partners_p4x_account_id_fkey", "p4x_partners", type_="foreignkey"
    )
    op.drop_constraint(
        "p4x_partners_p4x_specialcontact_id_fkey",
        "p4x_partners",
        type_="foreignkey",
    )
    op.drop_column("p4x_partners", "member_id")
    op.drop_column("p4x_partners", "contact_id")
    op.drop_column("p4x_partners", "p4x_account_id")
    op.drop_column("p4x_partners", "p4x_specialcontact_id")

    op.create_index(op.f("ix_p4x_partners_partner_id"), "p4x_partners", ["partner_id"])
