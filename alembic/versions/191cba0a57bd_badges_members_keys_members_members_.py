"""badges_members, keys_members, members_roles composite pk cutover

Revision ID: 191cba0a57bd
Revises: ec1af5390d0c
Create Date: 2026-09-01 20:15:00.000000

The three composite-PK junction tables (no surrogate `id` - the primary
key IS the column combination): badges_members, keys_members,
members_roles. Pattern (see section 1.2 of the migration plan): add
`*_uuid` columns for the integer FK halves, join-backfill them, then
atomically drop the old composite PK/FKs, rename the new columns into
place, and add the new composite PK/FKs - one migration per table, no
separate Phase A/C window (composite keys have no meaningful
"in-between" state).

members_roles keeps `role_id`/`startdate` untouched - roles.id is a
string primary key, entirely outside this migration series' scope (see
the plan's exclusion list), so only its member_id half moves. badges_members/
keys_members move both halves, onto badges(id_uuid)/keys(id_uuid)
respectively (neither table has its own UUID primary key yet - that's
slice 25 - referrers cut over onto the additive id_uuid column same as
every other Wave C slice).
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "191cba0a57bd"
down_revision: str | Sequence[str] | None = "ec1af5390d0c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _join_backfill(
    table: str, fk_col: str, tmp_col: str, parent_table: str, *, to_uuid: bool
) -> None:
    parent_source = "id" if to_uuid else "id_uuid"
    parent_target = "id_uuid" if to_uuid else "id"
    op.execute(
        f"UPDATE {table} a SET {tmp_col} = p.{parent_target} "  # noqa: S608
        f"FROM {parent_table} p WHERE a.{fk_col} = p.{parent_source}"
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- badges_members: member_id -> members.id_uuid, badge_id -> badges.id_uuid
    op.add_column(
        "badges_members", sa.Column("member_id_uuid", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "badges_members", sa.Column("badge_id_uuid", sa.Uuid(), nullable=True)
    )
    _join_backfill(
        "badges_members", "member_id", "member_id_uuid", "members", to_uuid=True
    )
    _join_backfill(
        "badges_members", "badge_id", "badge_id_uuid", "badges", to_uuid=True
    )
    op.drop_constraint("badges_members_pkey", "badges_members", type_="primary")
    op.drop_constraint(
        "badges_members_member_id_fkey", "badges_members", type_="foreignkey"
    )
    op.drop_constraint(
        "badges_members_badge_id_fkey", "badges_members", type_="foreignkey"
    )
    op.drop_column("badges_members", "member_id")
    op.drop_column("badges_members", "badge_id")
    op.alter_column("badges_members", "member_id_uuid", new_column_name="member_id")
    op.alter_column("badges_members", "badge_id_uuid", new_column_name="badge_id")
    op.alter_column("badges_members", "member_id", nullable=False)
    op.alter_column("badges_members", "badge_id", nullable=False)
    op.create_primary_key(
        "badges_members_pkey", "badges_members", ["member_id", "badge_id"]
    )
    op.create_foreign_key(
        "badges_members_member_id_fkey",
        "badges_members",
        "members",
        ["member_id"],
        ["id_uuid"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        "badges_members_badge_id_fkey",
        "badges_members",
        "badges",
        ["badge_id"],
        ["id_uuid"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )

    # --- keys_members: member_id -> members.id_uuid, key_id -> keys.id_uuid ---
    op.add_column("keys_members", sa.Column("member_id_uuid", sa.Uuid(), nullable=True))
    op.add_column("keys_members", sa.Column("key_id_uuid", sa.Uuid(), nullable=True))
    _join_backfill(
        "keys_members", "member_id", "member_id_uuid", "members", to_uuid=True
    )
    _join_backfill("keys_members", "key_id", "key_id_uuid", "keys", to_uuid=True)
    op.drop_constraint("keys_members_pkey", "keys_members", type_="primary")
    op.drop_constraint(
        "keys_members_member_id_fkey", "keys_members", type_="foreignkey"
    )
    op.drop_constraint("keys_members_key_id_fkey", "keys_members", type_="foreignkey")
    op.drop_column("keys_members", "member_id")
    op.drop_column("keys_members", "key_id")
    op.alter_column("keys_members", "member_id_uuid", new_column_name="member_id")
    op.alter_column("keys_members", "key_id_uuid", new_column_name="key_id")
    op.alter_column("keys_members", "member_id", nullable=False)
    op.alter_column("keys_members", "key_id", nullable=False)
    op.create_primary_key("keys_members_pkey", "keys_members", ["member_id", "key_id"])
    op.create_foreign_key(
        "keys_members_member_id_fkey",
        "keys_members",
        "members",
        ["member_id"],
        ["id_uuid"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        "keys_members_key_id_fkey",
        "keys_members",
        "keys",
        ["key_id"],
        ["id_uuid"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )

    # --- members_roles: member_id -> members.id_uuid only (role_id/startdate untouched)
    op.add_column(
        "members_roles", sa.Column("member_id_uuid", sa.Uuid(), nullable=True)
    )
    _join_backfill(
        "members_roles", "member_id", "member_id_uuid", "members", to_uuid=True
    )
    op.drop_constraint("members_roles_pkey", "members_roles", type_="primary")
    op.drop_constraint(
        "members_roles_member_id_fkey", "members_roles", type_="foreignkey"
    )
    op.drop_column("members_roles", "member_id")
    op.alter_column("members_roles", "member_id_uuid", new_column_name="member_id")
    op.alter_column("members_roles", "member_id", nullable=False)
    op.create_primary_key(
        "members_roles_pkey", "members_roles", ["member_id", "role_id", "startdate"]
    )
    op.create_foreign_key(
        "members_roles_member_id_fkey",
        "members_roles",
        "members",
        ["member_id"],
        ["id_uuid"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )


def downgrade() -> None:
    """Downgrade schema.

    Loss-free: every reverse join maps back through the parent's id_uuid
    to the exact same integer id it came from - no fresh sequence
    involved anywhere, unlike a Final-Cutover downgrade. Composite keys
    have no surrogate id to regenerate.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- Revert members_roles ------------------------------------------
    op.add_column(
        "members_roles", sa.Column("member_id_int", sa.Integer(), nullable=True)
    )
    _join_backfill(
        "members_roles", "member_id", "member_id_int", "members", to_uuid=False
    )
    op.drop_constraint("members_roles_pkey", "members_roles", type_="primary")
    op.drop_constraint(
        "members_roles_member_id_fkey", "members_roles", type_="foreignkey"
    )
    op.drop_column("members_roles", "member_id")
    op.alter_column("members_roles", "member_id_int", new_column_name="member_id")
    op.alter_column("members_roles", "member_id", nullable=False)
    op.create_primary_key(
        "members_roles_pkey", "members_roles", ["member_id", "role_id", "startdate"]
    )
    op.create_foreign_key(
        "members_roles_member_id_fkey",
        "members_roles",
        "members",
        ["member_id"],
        ["id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )

    # --- Revert keys_members ---------------------------------------------
    op.add_column(
        "keys_members", sa.Column("member_id_int", sa.Integer(), nullable=True)
    )
    op.add_column("keys_members", sa.Column("key_id_int", sa.Integer(), nullable=True))
    _join_backfill(
        "keys_members", "member_id", "member_id_int", "members", to_uuid=False
    )
    _join_backfill("keys_members", "key_id", "key_id_int", "keys", to_uuid=False)
    op.drop_constraint("keys_members_pkey", "keys_members", type_="primary")
    op.drop_constraint(
        "keys_members_member_id_fkey", "keys_members", type_="foreignkey"
    )
    op.drop_constraint("keys_members_key_id_fkey", "keys_members", type_="foreignkey")
    op.drop_column("keys_members", "member_id")
    op.drop_column("keys_members", "key_id")
    op.alter_column("keys_members", "member_id_int", new_column_name="member_id")
    op.alter_column("keys_members", "key_id_int", new_column_name="key_id")
    op.alter_column("keys_members", "member_id", nullable=False)
    op.alter_column("keys_members", "key_id", nullable=False)
    op.create_primary_key("keys_members_pkey", "keys_members", ["member_id", "key_id"])
    op.create_foreign_key(
        "keys_members_member_id_fkey",
        "keys_members",
        "members",
        ["member_id"],
        ["id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        "keys_members_key_id_fkey",
        "keys_members",
        "keys",
        ["key_id"],
        ["id"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )

    # --- Revert badges_members ------------------------------------------
    op.add_column(
        "badges_members", sa.Column("member_id_int", sa.Integer(), nullable=True)
    )
    op.add_column(
        "badges_members", sa.Column("badge_id_int", sa.Integer(), nullable=True)
    )
    _join_backfill(
        "badges_members", "member_id", "member_id_int", "members", to_uuid=False
    )
    _join_backfill(
        "badges_members", "badge_id", "badge_id_int", "badges", to_uuid=False
    )
    op.drop_constraint("badges_members_pkey", "badges_members", type_="primary")
    op.drop_constraint(
        "badges_members_member_id_fkey", "badges_members", type_="foreignkey"
    )
    op.drop_constraint(
        "badges_members_badge_id_fkey", "badges_members", type_="foreignkey"
    )
    op.drop_column("badges_members", "member_id")
    op.drop_column("badges_members", "badge_id")
    op.alter_column("badges_members", "member_id_int", new_column_name="member_id")
    op.alter_column("badges_members", "badge_id_int", new_column_name="badge_id")
    op.alter_column("badges_members", "member_id", nullable=False)
    op.alter_column("badges_members", "badge_id", nullable=False)
    op.create_primary_key(
        "badges_members_pkey", "badges_members", ["member_id", "badge_id"]
    )
    op.create_foreign_key(
        "badges_members_member_id_fkey",
        "badges_members",
        "members",
        ["member_id"],
        ["id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        "badges_members_badge_id_fkey",
        "badges_members",
        "badges",
        ["badge_id"],
        ["id"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )
