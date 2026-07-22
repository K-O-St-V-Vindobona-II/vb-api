"""check constraints

Revision ID: cb250c054945
Revises: 74d19e4af679
Create Date: 2026-07-22 11:28:48.234250

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cb250c054945"
down_revision: str | Sequence[str] | None = "74d19e4af679"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, constraint name, condition)
_CHECK_CONSTRAINTS: list[tuple[str, str, str]] = [
    (
        "members",
        "members_geburtsdatum_accuracy_check",
        "geburtsdatum_accuracy IS NULL OR geburtsdatum_accuracy BETWEEN 0 AND 3",
    ),
    (
        "members",
        "members_aufnahmedatum_accuracy_check",
        "aufnahmedatum_accuracy IS NULL OR aufnahmedatum_accuracy BETWEEN 0 AND 3",
    ),
    (
        "members",
        "members_branderdatum_accuracy_check",
        "branderdatum_accuracy IS NULL OR branderdatum_accuracy BETWEEN 0 AND 3",
    ),
    (
        "members",
        "members_burschungsdatum_accuracy_check",
        "burschungsdatum_accuracy IS NULL OR burschungsdatum_accuracy BETWEEN 0 AND 3",
    ),
    (
        "members",
        "members_philistrierungsdatum_accuracy_check",
        "philistrierungsdatum_accuracy IS NULL "
        "OR philistrierungsdatum_accuracy BETWEEN 0 AND 3",
    ),
    (
        "members",
        "members_entlassungsdatum_accuracy_check",
        "entlassungsdatum_accuracy IS NULL "
        "OR entlassungsdatum_accuracy BETWEEN 0 AND 3",
    ),
    (
        "members",
        "members_sterbedatum_accuracy_check",
        "sterbedatum_accuracy IS NULL OR sterbedatum_accuracy BETWEEN 0 AND 3",
    ),
    (
        "contacts",
        "contacts_datum_accuracy_check",
        "datum_accuracy IS NULL OR datum_accuracy BETWEEN 0 AND 3",
    ),
    (
        "members_badges",
        "members_badges_presentationdate_accuracy_check",
        "presentationdate_accuracy IS NULL "
        "OR presentationdate_accuracy BETWEEN 0 AND 3",
    ),
    (
        "members_keys",
        "members_keys_presentationdate_accuracy_check",
        "presentationdate_accuracy IS NULL "
        "OR presentationdate_accuracy BETWEEN 0 AND 3",
    ),
    (
        "members_roles",
        "members_roles_startdate_enddate_check",
        "enddate IS NULL OR startdate < enddate",
    ),
    (
        "p4x_summary_orders",
        "p4x_summary_orders_summary_start_end_check",
        "summary_end >= summary_start",
    ),
    (
        "archive_store_items",
        "archive_store_items_size_check",
        "size >= 0",
    ),
    (
        "archive_dirs",
        "archive_dirs_archive_dir_id_check",
        "archive_dir_id IS NULL OR archive_dir_id >= 0",
    ),
    (
        "archive_dirs",
        "archive_dirs_name_check",
        "length(name) BETWEEN 3 AND 64",
    ),
    (
        "archive_files",
        "archive_files_archive_dir_id_check",
        "archive_dir_id IS NULL OR archive_dir_id >= 0",
    ),
    (
        "archive_file_comments",
        "archive_file_comments_content_check",
        "content IS NULL OR length(content) BETWEEN 1 AND 1000",
    ),
    (
        "p4x_fees",
        "p4x_fees_fee_check",
        "fee >= 0",
    ),
    (
        "standesdb_images",
        "standesdb_images_size_check",
        "size IS NULL OR size >= 0",
    ),
    (
        "standesdb_images",
        "standesdb_images_width_check",
        "width IS NULL OR width > 0",
    ),
    (
        "standesdb_images",
        "standesdb_images_height_check",
        "height IS NULL OR height > 0",
    ),
    (
        "public_gallery_images",
        "public_gallery_images_size_check",
        "size >= 0",
    ),
    (
        "public_gallery_images",
        "public_gallery_images_width_height_check",
        "width > 0 AND height > 0",
    ),
    (
        "public_gallery_images",
        "public_gallery_images_sort_order_check",
        "sort_order >= 0",
    ),
    (
        "request_logs",
        "request_logs_memory_usage_check",
        "memory_usage >= 0",
    ),
    (
        "orgs",
        "orgs_order_check",
        '"order" IS NULL OR "order" >= 0',
    ),
    (
        "roles",
        "roles_order_check",
        '"order" IS NULL OR "order" >= 0',
    ),
    (
        "states",
        "states_order_check",
        '"order" IS NULL OR "order" >= 0',
    ),
    (
        "badges",
        "badges_order_check",
        '"order" IS NULL OR "order" >= 0',
    ),
    (
        "p4x_accounts",
        "p4x_accounts_iban_check",
        "iban ~ '^[A-Z]{2}[0-9]{2}[A-Z0-9 ]{4,}$'",
    ),
    (
        "p4x_accounts",
        "p4x_accounts_bic_check",
        "bic IS NULL OR bic ~ '^[A-Za-z0-9]{1,11}$'",
    ),
    (
        "p4x_categories",
        "p4x_categories_background_color_check",
        "background_color ~ '^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$'",
    ),
    (
        "p4x_categories",
        "p4x_categories_text_color_check",
        "text_color ~ '^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$'",
    ),
    (
        "p4x_category_filters",
        "p4x_category_filters_min_max_amount_check",
        "min_amount IS NULL OR max_amount IS NULL OR min_amount <= max_amount",
    ),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # id=80 is a dead filter (min_amount=100 > max_amount=-77): it can never
    # match a transaction and is deleted rather than "fixed", since there is
    # no correct value to guess. Required before the min/max CHECK below can
    # be applied cleanly.
    op.execute("DELETE FROM p4x_category_filters WHERE id = 80")

    for table, name, condition in _CHECK_CONSTRAINTS:
        op.create_check_constraint(name, table, condition)


def downgrade() -> None:
    """Downgrade schema."""
    for table, name, _condition in reversed(_CHECK_CONSTRAINTS):
        op.drop_constraint(name, table, type_="check")
