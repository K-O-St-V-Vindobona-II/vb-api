"""fk ondelete onupdate

Revision ID: 3f58e5fc7f5f
Revises: 4831bb012783
Create Date: 2026-07-21 19:57:08.088382

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f58e5fc7f5f"
down_revision: str | Sequence[str] | None = "4831bb012783"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.drop_constraint(
        "archive_file_comments_archive_file_id_fkey",
        "archive_file_comments",
        type_="foreignkey",
    )
    op.drop_constraint(
        "archive_file_comments_created_by_fkey",
        "archive_file_comments",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "archive_file_comments_archive_file_id_fkey",
        "archive_file_comments",
        "archive_files",
        ["archive_file_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "archive_file_comments_created_by_fkey",
        "archive_file_comments",
        "members",
        ["created_by"],
        ["id"],
        onupdate="CASCADE",
        ondelete="SET NULL",
    )

    op.drop_constraint(
        "archive_file_versions_archive_store_item_id_fkey",
        "archive_file_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "archive_file_versions_archive_file_id_fkey",
        "archive_file_versions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "archive_file_versions_archive_store_item_id_fkey",
        "archive_file_versions",
        "archive_store_items",
        ["archive_store_item_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "archive_file_versions_archive_file_id_fkey",
        "archive_file_versions",
        "archive_files",
        ["archive_file_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "archive_permissions_archive_dir_id_fkey",
        "archive_permissions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "archive_permissions_archive_dir_id_fkey",
        "archive_permissions",
        "archive_dirs",
        ["archive_dir_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "archive_store_items_created_by_fkey",
        "archive_store_items",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "archive_store_items_created_by_fkey",
        "archive_store_items",
        "members",
        ["created_by"],
        ["id"],
        onupdate="CASCADE",
        ondelete="SET NULL",
    )

    op.drop_constraint("contacts_org_id_fkey", "contacts", type_="foreignkey")
    op.create_foreign_key(
        "contacts_org_id_fkey",
        "contacts",
        "orgs",
        ["org_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "contacts_logs_contact_id_fkey", "contacts_logs", type_="foreignkey"
    )
    op.create_foreign_key(
        "contacts_logs_contact_id_fkey",
        "contacts_logs",
        "contacts",
        ["contact_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="SET NULL",
    )

    op.drop_constraint("members_state_id_fkey", "members", type_="foreignkey")
    # members_parent_id_fkey is absent on historically-grown Dev/Prod (added
    # to the model after those tables were created; create_all() never
    # retrofits constraints onto existing tables), but present on databases
    # built fresh from REV3 (e.g. CI). IF EXISTS keeps this migration valid
    # for both starting states.
    op.execute("ALTER TABLE members DROP CONSTRAINT IF EXISTS members_parent_id_fkey")
    op.drop_constraint("members_org_id_fkey", "members", type_="foreignkey")
    op.create_foreign_key(
        "members_state_id_fkey",
        "members",
        "states",
        ["state_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "members_parent_id_fkey",
        "members",
        "members",
        ["parent_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "members_org_id_fkey",
        "members",
        "orgs",
        ["org_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "members_badges_member_id_fkey", "members_badges", type_="foreignkey"
    )
    op.drop_constraint(
        "members_badges_badge_id_fkey", "members_badges", type_="foreignkey"
    )
    op.create_foreign_key(
        "members_badges_member_id_fkey",
        "members_badges",
        "members",
        ["member_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "members_badges_badge_id_fkey",
        "members_badges",
        "badges",
        ["badge_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )

    op.drop_constraint("members_keys_key_id_fkey", "members_keys", type_="foreignkey")
    op.drop_constraint(
        "members_keys_member_id_fkey", "members_keys", type_="foreignkey"
    )
    op.create_foreign_key(
        "members_keys_key_id_fkey",
        "members_keys",
        "keys",
        ["key_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "members_keys_member_id_fkey",
        "members_keys",
        "members",
        ["member_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="CASCADE",
    )

    # See members_parent_id_fkey comment above: same historically-missing-
    # on-Dev/Prod situation applies here.
    op.execute(
        "ALTER TABLE members_logs DROP CONSTRAINT IF EXISTS members_logs_member_id_fkey"
    )
    op.create_foreign_key(
        "members_logs_member_id_fkey",
        "members_logs",
        "members",
        ["member_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="SET NULL",
    )

    op.drop_constraint(
        "members_oauth2bindings_member_id_fkey",
        "members_oauth2bindings",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "members_oauth2bindings_member_id_fkey",
        "members_oauth2bindings",
        "members",
        ["member_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "members_roles_member_id_fkey", "members_roles", type_="foreignkey"
    )
    op.drop_constraint(
        "members_roles_role_id_fkey", "members_roles", type_="foreignkey"
    )
    op.create_foreign_key(
        "members_roles_member_id_fkey",
        "members_roles",
        "members",
        ["member_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "members_roles_role_id_fkey",
        "members_roles",
        "roles",
        ["role_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "p4x_category_directs_p4x_transaction_id_fkey",
        "p4x_category_directs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "p4x_category_directs_p4x_category_id_fkey",
        "p4x_category_directs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "p4x_category_directs_p4x_transaction_id_fkey",
        "p4x_category_directs",
        "p4x_transactions",
        ["p4x_transaction_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "p4x_category_directs_p4x_category_id_fkey",
        "p4x_category_directs",
        "p4x_categories",
        ["p4x_category_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "p4x_category_filter_hits_p4x_transaction_id_fkey",
        "p4x_category_filter_hits",
        type_="foreignkey",
    )
    op.drop_constraint(
        "p4x_category_filter_hits_p4x_category_filter_id_fkey",
        "p4x_category_filter_hits",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "p4x_category_filter_hits_p4x_transaction_id_fkey",
        "p4x_category_filter_hits",
        "p4x_transactions",
        ["p4x_transaction_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "p4x_category_filter_hits_p4x_category_filter_id_fkey",
        "p4x_category_filter_hits",
        "p4x_category_filters",
        ["p4x_category_filter_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "p4x_category_filters_p4x_category_id_fkey",
        "p4x_category_filters",
        type_="foreignkey",
    )
    op.drop_constraint(
        "p4x_category_filters_p4x_account_id_fkey",
        "p4x_category_filters",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "p4x_category_filters_p4x_category_id_fkey",
        "p4x_category_filters",
        "p4x_categories",
        ["p4x_category_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "p4x_category_filters_p4x_account_id_fkey",
        "p4x_category_filters",
        "p4x_accounts",
        ["p4x_account_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "p4x_summary_orders_ordered_by_fkey", "p4x_summary_orders", type_="foreignkey"
    )
    op.create_foreign_key(
        "p4x_summary_orders_ordered_by_fkey",
        "p4x_summary_orders",
        "members",
        ["ordered_by"],
        ["id"],
        onupdate="CASCADE",
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "p4x_transactions_p4x_account_id_fkey", "p4x_transactions", type_="foreignkey"
    )
    op.create_foreign_key(
        "p4x_transactions_p4x_account_id_fkey",
        "p4x_transactions",
        "p4x_accounts",
        ["p4x_account_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "p4x_transactions_p4x_account_id_fkey", "p4x_transactions", type_="foreignkey"
    )
    op.create_foreign_key(
        "p4x_transactions_p4x_account_id_fkey",
        "p4x_transactions",
        "p4x_accounts",
        ["p4x_account_id"],
        ["id"],
    )

    op.drop_constraint(
        "p4x_summary_orders_ordered_by_fkey", "p4x_summary_orders", type_="foreignkey"
    )
    op.create_foreign_key(
        "p4x_summary_orders_ordered_by_fkey",
        "p4x_summary_orders",
        "members",
        ["ordered_by"],
        ["id"],
    )

    op.drop_constraint(
        "p4x_category_filters_p4x_account_id_fkey",
        "p4x_category_filters",
        type_="foreignkey",
    )
    op.drop_constraint(
        "p4x_category_filters_p4x_category_id_fkey",
        "p4x_category_filters",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "p4x_category_filters_p4x_account_id_fkey",
        "p4x_category_filters",
        "p4x_accounts",
        ["p4x_account_id"],
        ["id"],
    )
    op.create_foreign_key(
        "p4x_category_filters_p4x_category_id_fkey",
        "p4x_category_filters",
        "p4x_categories",
        ["p4x_category_id"],
        ["id"],
    )

    op.drop_constraint(
        "p4x_category_filter_hits_p4x_category_filter_id_fkey",
        "p4x_category_filter_hits",
        type_="foreignkey",
    )
    op.drop_constraint(
        "p4x_category_filter_hits_p4x_transaction_id_fkey",
        "p4x_category_filter_hits",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "p4x_category_filter_hits_p4x_category_filter_id_fkey",
        "p4x_category_filter_hits",
        "p4x_category_filters",
        ["p4x_category_filter_id"],
        ["id"],
    )
    op.create_foreign_key(
        "p4x_category_filter_hits_p4x_transaction_id_fkey",
        "p4x_category_filter_hits",
        "p4x_transactions",
        ["p4x_transaction_id"],
        ["id"],
    )

    op.drop_constraint(
        "p4x_category_directs_p4x_category_id_fkey",
        "p4x_category_directs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "p4x_category_directs_p4x_transaction_id_fkey",
        "p4x_category_directs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "p4x_category_directs_p4x_category_id_fkey",
        "p4x_category_directs",
        "p4x_categories",
        ["p4x_category_id"],
        ["id"],
    )
    op.create_foreign_key(
        "p4x_category_directs_p4x_transaction_id_fkey",
        "p4x_category_directs",
        "p4x_transactions",
        ["p4x_transaction_id"],
        ["id"],
    )

    op.drop_constraint(
        "members_roles_role_id_fkey", "members_roles", type_="foreignkey"
    )
    op.drop_constraint(
        "members_roles_member_id_fkey", "members_roles", type_="foreignkey"
    )
    op.create_foreign_key(
        "members_roles_role_id_fkey", "members_roles", "roles", ["role_id"], ["id"]
    )
    op.create_foreign_key(
        "members_roles_member_id_fkey",
        "members_roles",
        "members",
        ["member_id"],
        ["id"],
    )

    op.drop_constraint(
        "members_oauth2bindings_member_id_fkey",
        "members_oauth2bindings",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "members_oauth2bindings_member_id_fkey",
        "members_oauth2bindings",
        "members",
        ["member_id"],
        ["id"],
    )

    op.drop_constraint(
        "members_logs_member_id_fkey", "members_logs", type_="foreignkey"
    )
    op.create_foreign_key(
        "members_logs_member_id_fkey",
        "members_logs",
        "members",
        ["member_id"],
        ["id"],
    )

    op.drop_constraint(
        "members_keys_member_id_fkey", "members_keys", type_="foreignkey"
    )
    op.drop_constraint("members_keys_key_id_fkey", "members_keys", type_="foreignkey")
    op.create_foreign_key(
        "members_keys_member_id_fkey",
        "members_keys",
        "members",
        ["member_id"],
        ["id"],
    )
    op.create_foreign_key(
        "members_keys_key_id_fkey", "members_keys", "keys", ["key_id"], ["id"]
    )

    op.drop_constraint(
        "members_badges_badge_id_fkey", "members_badges", type_="foreignkey"
    )
    op.drop_constraint(
        "members_badges_member_id_fkey", "members_badges", type_="foreignkey"
    )
    op.create_foreign_key(
        "members_badges_badge_id_fkey",
        "members_badges",
        "badges",
        ["badge_id"],
        ["id"],
    )
    op.create_foreign_key(
        "members_badges_member_id_fkey",
        "members_badges",
        "members",
        ["member_id"],
        ["id"],
    )

    op.drop_constraint("members_org_id_fkey", "members", type_="foreignkey")
    op.drop_constraint("members_parent_id_fkey", "members", type_="foreignkey")
    op.drop_constraint("members_state_id_fkey", "members", type_="foreignkey")
    op.create_foreign_key("members_org_id_fkey", "members", "orgs", ["org_id"], ["id"])
    op.create_foreign_key(
        "members_parent_id_fkey", "members", "members", ["parent_id"], ["id"]
    )
    op.create_foreign_key(
        "members_state_id_fkey", "members", "states", ["state_id"], ["id"]
    )

    op.drop_constraint(
        "contacts_logs_contact_id_fkey", "contacts_logs", type_="foreignkey"
    )
    op.create_foreign_key(
        "contacts_logs_contact_id_fkey",
        "contacts_logs",
        "contacts",
        ["contact_id"],
        ["id"],
    )

    op.drop_constraint("contacts_org_id_fkey", "contacts", type_="foreignkey")
    op.create_foreign_key(
        "contacts_org_id_fkey", "contacts", "orgs", ["org_id"], ["id"]
    )

    op.drop_constraint(
        "archive_store_items_created_by_fkey",
        "archive_store_items",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "archive_store_items_created_by_fkey",
        "archive_store_items",
        "members",
        ["created_by"],
        ["id"],
    )

    op.drop_constraint(
        "archive_permissions_archive_dir_id_fkey",
        "archive_permissions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "archive_permissions_archive_dir_id_fkey",
        "archive_permissions",
        "archive_dirs",
        ["archive_dir_id"],
        ["id"],
    )

    op.drop_constraint(
        "archive_file_versions_archive_file_id_fkey",
        "archive_file_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "archive_file_versions_archive_store_item_id_fkey",
        "archive_file_versions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "archive_file_versions_archive_file_id_fkey",
        "archive_file_versions",
        "archive_files",
        ["archive_file_id"],
        ["id"],
    )
    op.create_foreign_key(
        "archive_file_versions_archive_store_item_id_fkey",
        "archive_file_versions",
        "archive_store_items",
        ["archive_store_item_id"],
        ["id"],
    )

    op.drop_constraint(
        "archive_file_comments_created_by_fkey",
        "archive_file_comments",
        type_="foreignkey",
    )
    op.drop_constraint(
        "archive_file_comments_archive_file_id_fkey",
        "archive_file_comments",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "archive_file_comments_created_by_fkey",
        "archive_file_comments",
        "members",
        ["created_by"],
        ["id"],
    )
    op.create_foreign_key(
        "archive_file_comments_archive_file_id_fkey",
        "archive_file_comments",
        "archive_files",
        ["archive_file_id"],
        ["id"],
    )
