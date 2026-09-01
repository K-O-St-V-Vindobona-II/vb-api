"""public_site_settings id to uuid

Revision ID: 5c9769a76def
Revises: ad4d9aeff7b8
Create Date: 2026-09-01 14:31:07.636598

Singleton settings row (see PublicSiteSettings' docstring): unlike every
other table in this UUID-PK migration series, the new id is not generated
at runtime via `uuid.uuid7()` on insert - there is and will only ever be
exactly one row, so it gets a fixed, documented UUID literal instead. That
literal must stay in lockstep with
`app.models.public_site_settings.SETTINGS_ROW_ID`.
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c9769a76def"
down_revision: str | Sequence[str] | None = "ad4d9aeff7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "public_site_settings"
SINGLETON_CHECK = "public_site_settings_singleton_check"
# Fixed forever - must match app.models.public_site_settings.SETTINGS_ROW_ID.
SETTINGS_ROW_ID = "01a05cf3-e8f8-771b-9e7d-99181b476951"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- Phase A: additive prep -------------------------------------------
    op.add_column(TABLE, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    op.get_bind().execute(
        sa.text(f"UPDATE {TABLE} SET id_uuid = :new_id"),  # noqa: S608
        {"new_id": SETTINGS_ROW_ID},
    )
    op.alter_column(TABLE, "id_uuid", nullable=False)

    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {TABLE}_id_uuid_key ON {TABLE} (id_uuid)"
        )

    # --- Phase C: final cutover (no referrer table to wait for) -----------
    op.drop_constraint(SINGLETON_CHECK, TABLE, type_="check")
    op.drop_constraint(f"{TABLE}_pkey", TABLE, type_="primary")
    op.drop_column(TABLE, "id")
    op.execute(f"DROP SEQUENCE IF EXISTS {TABLE}_id_seq")
    op.alter_column(TABLE, "id_uuid", new_column_name="id")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {TABLE}_pkey "
        f"PRIMARY KEY USING INDEX {TABLE}_id_uuid_key"
    )
    op.create_check_constraint(SINGLETON_CHECK, TABLE, f"id = '{SETTINGS_ROW_ID}'")


def downgrade() -> None:
    """Downgrade schema.

    Loss-free, unlike every other table in this migration series: the
    singleton CHECK constraint guarantees exactly one row, and a freshly
    created sequence's first `nextval()` is 1 - the same value this row's
    integer id always had, so there is no ambiguity about which value to
    restore.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.drop_constraint(SINGLETON_CHECK, TABLE, type_="check")
    op.add_column(TABLE, sa.Column("id_int", sa.SmallInteger(), nullable=True))
    op.execute(f"CREATE SEQUENCE {TABLE}_id_seq OWNED BY {TABLE}.id_int")
    op.execute(f"UPDATE {TABLE} SET id_int = nextval('{TABLE}_id_seq')")  # noqa: S608
    op.alter_column(
        TABLE,
        "id_int",
        nullable=False,
        server_default=sa.text(f"nextval('{TABLE}_id_seq'::regclass)"),
    )
    op.drop_constraint(f"{TABLE}_pkey", TABLE, type_="primary")
    op.drop_column(TABLE, "id")
    op.alter_column(TABLE, "id_int", new_column_name="id")
    op.create_primary_key(f"{TABLE}_pkey", TABLE, ["id"])
    op.create_check_constraint(SINGLETON_CHECK, TABLE, "id = 1")
