"""archive_dirs final cutover, archive_dir_id sentinel-0 to NULL

Revision ID: 2727a9187d6e
Revises: 59abc303eb28
Create Date: 2026-09-02 14:00:00.000000

Two things happen in this migration, in a specific order:

1. archive_dirs.archive_dir_id (self-reference) and
   archive_files.archive_dir_id (parent reference) switch from an integer
   `0`-sentinel ("no parent"/"unfiled upload") to a plain nullable UUID
   column, each gaining a real FK for the first time - a `0` sentinel
   never had a real row to point at, so neither column could carry an FK
   before now. Both are converted while archive_dirs.id_uuid still exists
   as the additive Phase-A column, exactly like every Referrer-Cutover
   earlier in this series - the join-backfill needs id_uuid as the target
   and the still-integer id as the join key at the same time.
2. archive_dirs itself then gets its own Final-Cutover (id_uuid -> id). A
   PostgreSQL RENAME COLUMN updates the column an existing foreign key
   points to automatically, so the two FKs just created in step 1 plus
   the pre-existing archive_permissions_archive_dir_id_fkey all end up
   pointing at the new id column with no further changes.

downgrade() reverses this in the opposite order and, like every
Final-Cutover downgrade in this series, must drop every FK that depends
on archive_dirs' pkey index before reverting it (Postgres refuses
otherwise) and recreate them pointing at id_uuid again.

Neither archive_dir_id column had an index before this migration (only
the now-removed `archive_dir_id IS NULL OR archive_dir_id >= 0` CHECK,
which the UUID+NULL representation no longer needs at all - any UUID is
valid and NULL is explicitly allowed). Both get one now, since
archive_dir_id is filtered on in most directory-listing queries;
downgrade() drops them again to restore the exact pre-migration shape.
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2727a9187d6e"
down_revision: str | Sequence[str] | None = "59abc303eb28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # Step 1: archive_dirs.archive_dir_id (self-reference), int sentinel-0 -> UUID NULL
    op.add_column(
        "archive_dirs", sa.Column("archive_dir_id_uuid", sa.Uuid(), nullable=True)
    )
    op.execute(
        "UPDATE archive_dirs AS child "
        "SET archive_dir_id_uuid = parent.id_uuid "
        "FROM archive_dirs AS parent "
        "WHERE child.archive_dir_id = parent.id AND child.archive_dir_id != 0"
    )
    op.drop_constraint(
        "archive_dirs_archive_dir_id_check", "archive_dirs", type_="check"
    )
    op.drop_column("archive_dirs", "archive_dir_id")
    op.alter_column(
        "archive_dirs", "archive_dir_id_uuid", new_column_name="archive_dir_id"
    )
    op.create_index(
        "ix_archive_dirs_archive_dir_id", "archive_dirs", ["archive_dir_id"]
    )
    op.create_foreign_key(
        "archive_dirs_archive_dir_id_fkey",
        "archive_dirs",
        "archive_dirs",
        ["archive_dir_id"],
        ["id_uuid"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )

    # Step 2: archive_files.archive_dir_id (parent ref), int sentinel-0 -> UUID NULL
    op.add_column(
        "archive_files", sa.Column("archive_dir_id_uuid", sa.Uuid(), nullable=True)
    )
    op.execute(
        "UPDATE archive_files AS f "
        "SET archive_dir_id_uuid = d.id_uuid "
        "FROM archive_dirs AS d "
        "WHERE f.archive_dir_id = d.id AND f.archive_dir_id != 0"
    )
    op.drop_constraint(
        "archive_files_archive_dir_id_check", "archive_files", type_="check"
    )
    op.drop_column("archive_files", "archive_dir_id")
    op.alter_column(
        "archive_files", "archive_dir_id_uuid", new_column_name="archive_dir_id"
    )
    op.create_index(
        "ix_archive_files_archive_dir_id", "archive_files", ["archive_dir_id"]
    )
    op.create_foreign_key(
        "archive_files_archive_dir_id_fkey",
        "archive_files",
        "archive_dirs",
        ["archive_dir_id"],
        ["id_uuid"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )

    # Step 3: archive_dirs own PK Final-Cutover (id_uuid -> id). RENAME COLUMN
    # re-points archive_permissions_archive_dir_id_fkey (pre-existing) and
    # the two FKs from steps 1+2 above automatically - no referrer touch
    # needed.
    op.drop_constraint("archive_dirs_pkey", "archive_dirs", type_="primary")
    op.drop_column("archive_dirs", "id")
    op.execute("DROP SEQUENCE IF EXISTS archive_dirs_id_seq")
    op.alter_column("archive_dirs", "id_uuid", new_column_name="id")
    op.execute(
        "ALTER TABLE archive_dirs ADD CONSTRAINT archive_dirs_pkey "
        "PRIMARY KEY USING INDEX archive_dirs_id_uuid_key"
    )


def downgrade() -> None:
    """Downgrade schema.

    Not loss-free - a freshly created sequence has no relationship to any
    UUID that may already have circulated, same caveat as every other
    Final-Cutover downgrade in this series. Emergency rollback shortly
    after deploy only, never a production path.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")

    # Step 3 reverse: drop every FK depending on archive_dirs' pkey index
    # before reverting it, then rebuild the integer id + sequence and
    # recreate all three FKs pointing at id_uuid again.
    op.drop_constraint(
        "archive_permissions_archive_dir_id_fkey",
        "archive_permissions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "archive_dirs_archive_dir_id_fkey", "archive_dirs", type_="foreignkey"
    )
    op.drop_constraint(
        "archive_files_archive_dir_id_fkey", "archive_files", type_="foreignkey"
    )

    op.drop_constraint("archive_dirs_pkey", "archive_dirs", type_="primary")
    op.alter_column("archive_dirs", "id", new_column_name="id_uuid")
    op.add_column("archive_dirs", sa.Column("id", sa.Integer(), nullable=True))
    op.execute("CREATE SEQUENCE archive_dirs_id_seq OWNED BY archive_dirs.id")
    op.execute("UPDATE archive_dirs SET id = nextval('archive_dirs_id_seq')")
    op.alter_column(
        "archive_dirs",
        "id",
        nullable=False,
        server_default=sa.text("nextval('archive_dirs_id_seq'::regclass)"),
    )
    op.create_primary_key("archive_dirs_pkey", "archive_dirs", ["id"])
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY archive_dirs_id_uuid_key "
            "ON archive_dirs (id_uuid)"
        )

    op.create_foreign_key(
        "archive_permissions_archive_dir_id_fkey",
        "archive_permissions",
        "archive_dirs",
        ["archive_dir_id"],
        ["id_uuid"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )

    # Step 2 reverse: archive_files.archive_dir_id, UUID -> int sentinel-0.
    # archive_files_archive_dir_id_fkey was already dropped in the step-3-
    # reverse block above (and, unlike archive_permissions', never
    # recreated there - it doesn't survive this migration's downgrade at
    # all) so there is nothing left to drop here.
    op.drop_index("ix_archive_files_archive_dir_id", table_name="archive_files")
    op.add_column(
        "archive_files", sa.Column("archive_dir_id_int", sa.Integer(), nullable=True)
    )
    op.execute(
        "UPDATE archive_files AS f "
        "SET archive_dir_id_int = d.id "
        "FROM archive_dirs AS d "
        "WHERE f.archive_dir_id = d.id_uuid"
    )
    op.execute(
        "UPDATE archive_files SET archive_dir_id_int = 0 "
        "WHERE archive_dir_id_int IS NULL"
    )
    op.drop_column("archive_files", "archive_dir_id")
    op.alter_column(
        "archive_files", "archive_dir_id_int", new_column_name="archive_dir_id"
    )
    op.create_check_constraint(
        "archive_files_archive_dir_id_check",
        "archive_files",
        "archive_dir_id IS NULL OR archive_dir_id >= 0",
    )

    # Step 1 reverse: archive_dirs.archive_dir_id (self-ref), UUID -> int sentinel-0.
    # archive_dirs_archive_dir_id_fkey was already dropped in the step-3-
    # reverse block above and never recreated there, same reasoning as
    # archive_files_archive_dir_id_fkey.
    op.drop_index("ix_archive_dirs_archive_dir_id", table_name="archive_dirs")
    op.add_column(
        "archive_dirs", sa.Column("archive_dir_id_int", sa.Integer(), nullable=True)
    )
    op.execute(
        "UPDATE archive_dirs AS child "
        "SET archive_dir_id_int = parent.id "
        "FROM archive_dirs AS parent "
        "WHERE child.archive_dir_id = parent.id_uuid"
    )
    op.execute(
        "UPDATE archive_dirs SET archive_dir_id_int = 0 "
        "WHERE archive_dir_id_int IS NULL"
    )
    op.drop_column("archive_dirs", "archive_dir_id")
    op.alter_column(
        "archive_dirs", "archive_dir_id_int", new_column_name="archive_dir_id"
    )
    op.create_check_constraint(
        "archive_dirs_archive_dir_id_check",
        "archive_dirs",
        "archive_dir_id IS NULL OR archive_dir_id >= 0",
    )
