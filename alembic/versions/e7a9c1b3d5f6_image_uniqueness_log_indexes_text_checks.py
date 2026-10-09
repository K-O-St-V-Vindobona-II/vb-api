"""standesdb images: unique per owner and one default, log time indexes, text checks

Revision ID: e7a9c1b3d5f6
Revises: e1b3c5d7f9a2
Create Date: 2026-10-10 11:00:00.000000

1. standesdb_images_owner_hash_active_uniq was meant to stop one owner holding
   the same file twice, but it is one unique index over (hash, member, contact)
   and exactly one of the two owner columns is always NULL: a unique index
   treats NULLs as different, so it blocked nothing. It is replaced by one
   partial unique index per kind of owner, and two more allow one default
   image per owner.
2. request_logs and sent_emails had no index on created_at, although the
   activity overview reads a month, the mail list sorts newest first and the
   retention purge deletes by age. The combined index (member_id, created_at)
   replaces the single-column index on member_id, whose leading column it
   covers. Both tables are small enough to be indexed inside the migration's
   transaction, which keeps the revision atomic.
3. The site heading, the gallery heading and the social link label are checked
   for a non-space character like the texts of the earlier public content
   migration.

All checks of existing data run first: when one fails, nothing is changed, no
lock is taken and the error lists the offending rows.
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a9c1b3d5f6"
down_revision: str | Sequence[str] | None = "e1b3c5d7f9a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEXT_CHECKS = (
    (
        "public_site_settings",
        "public_site_settings_heading_check",
        "about_video_heading",
    ),
    (
        "public_site_settings",
        "public_site_settings_gallery_heading_check",
        "gallery_heading",
    ),
    ("public_site_social_links", "public_site_social_links_label_check", "label"),
)
_OWNERS = ("member", "contact")


def _refuse_if_any(rows: Sequence[object], what: str) -> None:
    if rows:
        msg = f"{what}: {list(rows)}"
        raise RuntimeError(msg)


def _refuse_bad_data(bind: sa.Connection) -> None:
    for owner in _OWNERS:
        column = f"owner_{owner}_id"
        _refuse_if_any(
            bind.execute(
                sa.text(
                    f"SELECT {column}, sha256_hash FROM standesdb_images "  # noqa: S608
                    f"WHERE deleted_at IS NULL AND {column} IS NOT NULL "
                    f"GROUP BY {column}, sha256_hash HAVING count(*) > 1"
                )
            ).all(),
            f"Owners ({owner}) holding the same image twice",
        )
        _refuse_if_any(
            bind.execute(
                sa.text(
                    f"SELECT {column} FROM standesdb_images "  # noqa: S608
                    f'WHERE "default" AND deleted_at IS NULL AND {column} IS NOT NULL '
                    f"GROUP BY {column} HAVING count(*) > 1"
                )
            )
            .scalars()
            .all(),
            f"Owners ({owner}) with more than one default image",
        )
    for table, _constraint, column in _TEXT_CHECKS:
        _refuse_if_any(
            bind.execute(
                sa.text(
                    f"SELECT id FROM {table} "  # noqa: S608
                    f"WHERE NOT ({column} ~ '[^[:space:]]')"
                )
            )
            .scalars()
            .all(),
            f"{table}.{column} holds only blanks in rows",
        )


def upgrade() -> None:
    """Upgrade schema."""
    _refuse_bad_data(op.get_bind())

    op.execute("SET LOCAL lock_timeout = '5s'")

    op.drop_index(
        "standesdb_images_owner_hash_active_uniq", table_name="standesdb_images"
    )
    for owner in _OWNERS:
        column = f"owner_{owner}_id"
        op.create_index(
            f"standesdb_images_{owner}_hash_active_uniq",
            "standesdb_images",
            ["sha256_hash", column],
            unique=True,
            postgresql_where=sa.text(f"deleted_at IS NULL AND {column} IS NOT NULL"),
        )
        op.create_index(
            f"standesdb_images_{owner}_default_uniq",
            "standesdb_images",
            [column],
            unique=True,
            postgresql_where=sa.text(
                f'"default" AND deleted_at IS NULL AND {column} IS NOT NULL'
            ),
        )
    for table, constraint, column in _TEXT_CHECKS:
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, f"{column} ~ '[^[:space:]]'")

    op.create_index("ix_request_logs_created_at", "request_logs", ["created_at"])
    op.create_index(
        "ix_request_logs_member_id_created_at",
        "request_logs",
        ["member_id", "created_at"],
    )
    op.drop_index("ix_request_logs_member_id", table_name="request_logs")
    op.create_index("ix_sent_emails_created_at", "sent_emails", ["created_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.drop_index("ix_sent_emails_created_at", table_name="sent_emails")
    op.create_index("ix_request_logs_member_id", "request_logs", ["member_id"])
    op.drop_index("ix_request_logs_member_id_created_at", table_name="request_logs")
    op.drop_index("ix_request_logs_created_at", table_name="request_logs")

    for table, constraint, column in _TEXT_CHECKS:
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, f"char_length({column}) > 0")
    for owner in _OWNERS:
        op.drop_index(
            f"standesdb_images_{owner}_default_uniq", table_name="standesdb_images"
        )
        op.drop_index(
            f"standesdb_images_{owner}_hash_active_uniq", table_name="standesdb_images"
        )
    op.create_index(
        "standesdb_images_owner_hash_active_uniq",
        "standesdb_images",
        ["sha256_hash", "owner_member_id", "owner_contact_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
