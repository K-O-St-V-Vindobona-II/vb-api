"""archive_files final cutover

Revision ID: 115c679b7348
Revises: 03c2395ca34e
Create Date: 2026-09-02 10:05:00.000000

archive_files completed its Phase A in
673aa46dc3b3_archive_files_phase_a_and_archive_.py and its only referrer
(archive_file_comments.archive_file_id) already points at id_uuid, cut
over in dd8661641df7. Own-PK-only Final-Cutover, same shape as
03c2395ca34e's three tables - a PostgreSQL RENAME COLUMN updates the
column an existing foreign key points to automatically, so
archive_file_comments needs no changes here.
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "115c679b7348"
down_revision: str | Sequence[str] | None = "03c2395ca34e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "archive_files"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint(f"{TABLE}_pkey", TABLE, type_="primary")
    op.drop_column(TABLE, "id")
    op.execute(f"DROP SEQUENCE IF EXISTS {TABLE}_id_seq")
    op.alter_column(TABLE, "id_uuid", new_column_name="id")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {TABLE}_pkey "
        f"PRIMARY KEY USING INDEX {TABLE}_id_uuid_key"
    )


def downgrade() -> None:
    """Downgrade schema.

    Not loss-free - a freshly created sequence has no relationship to any
    UUID that may already have circulated, same caveat as every other
    Final-Cutover downgrade in this series. Emergency rollback shortly
    after deploy only, never a production path.

    archive_file_comments.archive_file_id already has a live FK pointing
    at archive_files' primary key (added in dd8661641df7) - unlike the
    upgrade direction, Postgres can't just follow a RENAME COLUMN here,
    since dropping the primary key itself is blocked while a dependent FK
    still relies on its index. That FK is dropped before the PK is
    reverted and recreated against id_uuid afterward, restoring the exact
    shape it had at dd8661641df7.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint(
        "archive_file_comments_archive_file_id_fkey",
        "archive_file_comments",
        type_="foreignkey",
    )
    op.drop_constraint(f"{TABLE}_pkey", TABLE, type_="primary")
    op.alter_column(TABLE, "id", new_column_name="id_uuid")
    op.add_column(TABLE, sa.Column("id", sa.Integer(), nullable=True))
    op.execute(f"CREATE SEQUENCE {TABLE}_id_seq OWNED BY {TABLE}.id")
    op.execute(f"UPDATE {TABLE} SET id = nextval('{TABLE}_id_seq')")  # noqa: S608
    op.alter_column(
        TABLE,
        "id",
        nullable=False,
        server_default=sa.text(f"nextval('{TABLE}_id_seq'::regclass)"),
    )
    op.create_primary_key(f"{TABLE}_pkey", TABLE, ["id"])
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {TABLE}_id_uuid_key ON {TABLE} (id_uuid)"
        )
    op.create_foreign_key(
        "archive_file_comments_archive_file_id_fkey",
        "archive_file_comments",
        TABLE,
        ["archive_file_id"],
        ["id_uuid"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )
