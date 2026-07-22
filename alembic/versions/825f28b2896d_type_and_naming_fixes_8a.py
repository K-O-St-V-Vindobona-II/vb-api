"""type and naming fixes 8a

Revision ID: 825f28b2896d
Revises: ebc37d20860a
Create Date: 2026-07-22 23:24:33.003795

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "825f28b2896d"
down_revision: str | Sequence[str] | None = "ebc37d20860a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- standesdb_images.default: int (0/1) -> bool ---
    op.alter_column(
        "standesdb_images",
        "default",
        existing_type=sa.Integer(),
        type_=sa.Boolean(),
        existing_nullable=True,
        nullable=False,
        server_default=sa.false(),
        postgresql_using='"default" <> 0',
    )

    # --- sha256_hash: unify length to VARCHAR(64) everywhere, rename the
    # one outlier column, add a partial per-owner uniqueness guard on
    # standesdb_images (see model docstring for why it's not a plain
    # global UNIQUE) ---
    op.alter_column(
        "public_gallery_images",
        "sha256_hash",
        existing_type=sa.String(),
        type_=sa.String(64),
        existing_nullable=False,
    )
    op.alter_column(
        "standesdb_images",
        "sha256_hash",
        existing_type=sa.String(),
        type_=sa.String(64),
        existing_nullable=False,
    )
    op.create_index(
        "standesdb_images_owner_hash_active_uniq",
        "standesdb_images",
        ["sha256_hash", "owner_member_id", "owner_contact_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.alter_column(
        "p4x_transactions",
        "sha256hash",
        new_column_name="sha256_hash",
        existing_type=sa.String(),
    )
    op.alter_column(
        "p4x_transactions",
        "sha256_hash",
        existing_type=sa.String(),
        type_=sa.String(64),
        existing_nullable=False,
    )
    op.execute(
        "ALTER TABLE p4x_transactions RENAME CONSTRAINT"
        " p4x_transactions_sha256hash_key TO p4x_transactions_sha256_hash_key"
    )

    # --- junction-table alphabetical naming (CLAUDE.md: m:n tables named
    # alphabetically, e.g. article_tag not tag_article) ---
    op.rename_table("members_badges", "badges_members")
    op.execute(
        "ALTER TABLE badges_members RENAME CONSTRAINT members_badges_pkey"
        " TO badges_members_pkey"
    )
    op.execute(
        "ALTER TABLE badges_members RENAME CONSTRAINT"
        " members_badges_presentationdate_accuracy_check"
        " TO badges_members_presentationdate_accuracy_check"
    )
    op.execute(
        "ALTER TABLE badges_members RENAME CONSTRAINT"
        " members_badges_member_id_fkey TO badges_members_member_id_fkey"
    )
    op.execute(
        "ALTER TABLE badges_members RENAME CONSTRAINT"
        " members_badges_badge_id_fkey TO badges_members_badge_id_fkey"
    )

    op.rename_table("members_keys", "keys_members")
    op.execute(
        "ALTER TABLE keys_members RENAME CONSTRAINT members_keys_pkey"
        " TO keys_members_pkey"
    )
    op.execute(
        "ALTER TABLE keys_members RENAME CONSTRAINT"
        " members_keys_presentationdate_accuracy_check"
        " TO keys_members_presentationdate_accuracy_check"
    )
    op.execute(
        "ALTER TABLE keys_members RENAME CONSTRAINT"
        " members_keys_member_id_fkey TO keys_members_member_id_fkey"
    )
    op.execute(
        "ALTER TABLE keys_members RENAME CONSTRAINT"
        " members_keys_key_id_fkey TO keys_members_key_id_fkey"
    )

    # --- p4x_specialcontacts -> p4x_special_contacts (underscore style
    # consistency with the rest of the p4x_* tables) ---
    op.rename_table("p4x_specialcontacts", "p4x_special_contacts")
    op.execute(
        "ALTER TABLE p4x_special_contacts RENAME CONSTRAINT"
        " p4x_specialcontacts_pkey TO p4x_special_contacts_pkey"
    )
    op.execute(
        "ALTER TABLE p4x_partners RENAME CONSTRAINT"
        " p4x_partners_p4x_specialcontact_id_fkey"
        " TO p4x_partners_p4x_special_contact_id_fkey"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.execute(
        "ALTER TABLE p4x_partners RENAME CONSTRAINT"
        " p4x_partners_p4x_special_contact_id_fkey"
        " TO p4x_partners_p4x_specialcontact_id_fkey"
    )
    op.execute(
        "ALTER TABLE p4x_special_contacts RENAME CONSTRAINT"
        " p4x_special_contacts_pkey TO p4x_specialcontacts_pkey"
    )
    op.rename_table("p4x_special_contacts", "p4x_specialcontacts")

    op.execute(
        "ALTER TABLE keys_members RENAME CONSTRAINT"
        " keys_members_key_id_fkey TO members_keys_key_id_fkey"
    )
    op.execute(
        "ALTER TABLE keys_members RENAME CONSTRAINT"
        " keys_members_member_id_fkey TO members_keys_member_id_fkey"
    )
    op.execute(
        "ALTER TABLE keys_members RENAME CONSTRAINT"
        " keys_members_presentationdate_accuracy_check"
        " TO members_keys_presentationdate_accuracy_check"
    )
    op.execute(
        "ALTER TABLE keys_members RENAME CONSTRAINT keys_members_pkey"
        " TO members_keys_pkey"
    )
    op.rename_table("keys_members", "members_keys")

    op.execute(
        "ALTER TABLE badges_members RENAME CONSTRAINT"
        " badges_members_badge_id_fkey TO members_badges_badge_id_fkey"
    )
    op.execute(
        "ALTER TABLE badges_members RENAME CONSTRAINT"
        " badges_members_member_id_fkey TO members_badges_member_id_fkey"
    )
    op.execute(
        "ALTER TABLE badges_members RENAME CONSTRAINT"
        " badges_members_presentationdate_accuracy_check"
        " TO members_badges_presentationdate_accuracy_check"
    )
    op.execute(
        "ALTER TABLE badges_members RENAME CONSTRAINT badges_members_pkey"
        " TO members_badges_pkey"
    )
    op.rename_table("badges_members", "members_badges")

    op.execute(
        "ALTER TABLE p4x_transactions RENAME CONSTRAINT"
        " p4x_transactions_sha256_hash_key TO p4x_transactions_sha256hash_key"
    )
    op.alter_column(
        "p4x_transactions",
        "sha256_hash",
        existing_type=sa.String(64),
        type_=sa.String(),
        existing_nullable=False,
    )
    op.alter_column(
        "p4x_transactions",
        "sha256_hash",
        new_column_name="sha256hash",
        existing_type=sa.String(),
    )
    op.drop_index(
        "standesdb_images_owner_hash_active_uniq", table_name="standesdb_images"
    )
    op.alter_column(
        "standesdb_images",
        "sha256_hash",
        existing_type=sa.String(64),
        type_=sa.String(),
        existing_nullable=False,
    )
    op.alter_column(
        "public_gallery_images",
        "sha256_hash",
        existing_type=sa.String(64),
        type_=sa.String(),
        existing_nullable=False,
    )

    op.alter_column(
        "standesdb_images",
        "default",
        existing_type=sa.Boolean(),
        server_default=None,
    )
    op.alter_column(
        "standesdb_images",
        "default",
        existing_type=sa.Boolean(),
        type_=sa.Integer(),
        existing_nullable=False,
        nullable=True,
        postgresql_using='CASE WHEN "default" THEN 1 ELSE 0 END',
    )
