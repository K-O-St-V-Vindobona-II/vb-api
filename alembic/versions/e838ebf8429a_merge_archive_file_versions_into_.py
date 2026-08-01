"""merge archive file versions into archive files

Revision ID: e838ebf8429a
Revises: aaee376c8a39
Create Date: 2026-08-01 21:53:18.983820

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e838ebf8429a"
down_revision: str | Sequence[str] | None = "aaee376c8a39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # File versioning was removed as a feature during the SQLite->Postgres
    # migration: upload_file() is the only creator of ArchiveFile rows and
    # always creates exactly one ArchiveFile + one ArchiveFileVersion + one
    # ArchiveStoreItem atomically. Of 27,927 files, only 1 ever had more than
    # one version (a legacy artifact). archive_file_versions now only adds
    # indirection for a 1:1 relationship - replaced with a direct FK.
    op.add_column(
        "archive_files",
        sa.Column("archive_store_item_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "archive_files",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "archive_files",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Per file: prefer the active version, else (legacy data missing the
    # active flag) the lowest id - mirrors archive_service._active_store_item()'s
    # fallback exactly, but deterministically.
    op.execute(
        """
        UPDATE archive_files af
        SET archive_store_item_id = sub.archive_store_item_id
        FROM (
            SELECT DISTINCT ON (archive_file_id) archive_file_id, archive_store_item_id
            FROM archive_file_versions
            ORDER BY archive_file_id, active DESC NULLS LAST, id ASC
        ) sub
        WHERE af.id = sub.archive_file_id
        """
    )
    # The file and its store item are created atomically in the same
    # transaction by upload_file(), so the store item's created_at is the
    # exact historical timestamp, not an approximation.
    op.execute(
        """
        UPDATE archive_files af
        SET created_at = asi.created_at, updated_at = asi.created_at
        FROM archive_store_items asi
        WHERE af.archive_store_item_id = asi.id
        """
    )

    op.alter_column("archive_files", "archive_store_item_id", nullable=False)
    op.create_foreign_key(
        "archive_files_archive_store_item_id_fkey",
        "archive_files",
        "archive_store_items",
        ["archive_store_item_id"],
        ["id"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )
    op.execute(
        "CREATE TRIGGER archive_files_set_updated_at "
        "BEFORE UPDATE ON archive_files "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    op.drop_table("archive_file_versions")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP TRIGGER IF EXISTS archive_files_set_updated_at ON archive_files")

    op.create_table(
        "archive_file_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("archive_file_id", sa.Integer(), nullable=False),
        sa.Column("archive_store_item_id", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=True),
    )
    op.create_foreign_key(
        "archive_file_versions_archive_file_id_fkey",
        "archive_file_versions",
        "archive_files",
        ["archive_file_id"],
        ["id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        "archive_file_versions_archive_store_item_id_fkey",
        "archive_file_versions",
        "archive_store_items",
        ["archive_store_item_id"],
        ["id"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )
    # Downgrade deliberately loses the multi-reference/inactive-version
    # history: every file gets exactly one active=true version row back.
    op.execute(
        "INSERT INTO archive_file_versions "
        "(archive_file_id, archive_store_item_id, active) "
        "SELECT id, archive_store_item_id, true FROM archive_files"
    )

    op.drop_constraint(
        "archive_files_archive_store_item_id_fkey", "archive_files", type_="foreignkey"
    )
    op.drop_column("archive_files", "archive_store_item_id")
    op.drop_column("archive_files", "created_at")
    op.drop_column("archive_files", "updated_at")
