"""member_change_requests id, member_id, resolved_by cutover

Revision ID: ec1af5390d0c
Revises: bc095b5fb813
Create Date: 2026-09-01 19:20:00.000000

member_change_requests is a leaf table with no referrers of its own, so it
gets its own Final-Cutover (Phase A+C) AND both of its members FKs
(member_id, resolved_by) cut over in the same migration - same reasoning
as bc095b5fb813's three leaf tables. Both FKs now point at
members(id_uuid), not members(id) - members itself won't have a UUID
primary key until its own Final-Cutover (slice 32) - with the exact
ondelete/onupdate strategy each FK had before.

member_id carries two indexes that both get rebuilt on the new column:
the plain lookup index (ix_member_change_requests_member_id) AND the
partial unique index enforcing "at most one pending request per member"
(member_change_requests_member_pending_uniq, WHERE status = 'pending') -
a real business rule, not just a redundant duplicate like the ones
dropped-and-not-rebuilt in earlier slices. Dropping a column drops every
index defined on it, so both must be explicitly recreated.
"""

import uuid
from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ec1af5390d0c"
down_revision: str | Sequence[str] | None = "bc095b5fb813"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "member_change_requests"
BATCH_SIZE = 5000


def _batched_uuid_backfill() -> None:
    bind = op.get_bind()
    while True:
        rows = bind.execute(
            sa.text(
                f"SELECT id FROM {TABLE} WHERE id_uuid IS NULL LIMIT :limit"  # noqa: S608
            ),
            {"limit": BATCH_SIZE},
        ).fetchall()
        if not rows:
            return
        for row in rows:
            bind.execute(
                sa.text(f"UPDATE {TABLE} SET id_uuid = :new_id WHERE id = :old_id"),  # noqa: S608
                {"new_id": uuid.uuid7(), "old_id": row.id},
            )


def _join_backfill(fk_col: str, tmp_col: str, *, to_uuid: bool) -> None:
    parent_col = "id" if to_uuid else "id_uuid"
    new_col = "id_uuid" if to_uuid else "id"
    op.execute(
        f"UPDATE {TABLE} a SET {tmp_col} = m.{new_col} "  # noqa: S608
        f"FROM members m WHERE a.{fk_col} = m.{parent_col}"
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- Phase A+C: own primary key --------------------------------------
    op.add_column(TABLE, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    _batched_uuid_backfill()
    op.alter_column(TABLE, "id_uuid", nullable=False)
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {TABLE}_id_uuid_key ON {TABLE} (id_uuid)"
        )
    op.drop_constraint(f"{TABLE}_pkey", TABLE, type_="primary")
    op.drop_column(TABLE, "id")
    op.execute(f"DROP SEQUENCE IF EXISTS {TABLE}_id_seq")
    op.alter_column(TABLE, "id_uuid", new_column_name="id")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {TABLE}_pkey "
        f"PRIMARY KEY USING INDEX {TABLE}_id_uuid_key"
    )

    # --- Phase B: member_id -> members.id_uuid (NOT NULL) -----------------
    op.add_column(TABLE, sa.Column("member_id_uuid", sa.Uuid(), nullable=True))
    _join_backfill("member_id", "member_id_uuid", to_uuid=True)
    op.drop_constraint(
        "member_change_requests_member_id_fkey", TABLE, type_="foreignkey"
    )
    op.drop_column(TABLE, "member_id")
    op.alter_column(TABLE, "member_id_uuid", new_column_name="member_id")
    op.alter_column(TABLE, "member_id", nullable=False)
    op.create_index("ix_member_change_requests_member_id", TABLE, ["member_id"])
    op.execute(
        f"CREATE UNIQUE INDEX member_change_requests_member_pending_uniq "
        f"ON {TABLE} (member_id) WHERE status = 'pending'"
    )
    op.create_foreign_key(
        "member_change_requests_member_id_fkey",
        TABLE,
        "members",
        ["member_id"],
        ["id_uuid"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )

    # --- Phase B: resolved_by -> members.id_uuid (nullable) ---------------
    op.add_column(TABLE, sa.Column("resolved_by_uuid", sa.Uuid(), nullable=True))
    _join_backfill("resolved_by", "resolved_by_uuid", to_uuid=True)
    op.drop_constraint(
        "member_change_requests_resolved_by_fkey", TABLE, type_="foreignkey"
    )
    op.drop_column(TABLE, "resolved_by")
    op.alter_column(TABLE, "resolved_by_uuid", new_column_name="resolved_by")
    op.create_foreign_key(
        "member_change_requests_resolved_by_fkey",
        TABLE,
        "members",
        ["resolved_by"],
        ["id_uuid"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def downgrade() -> None:
    """Downgrade schema.

    The two FK halves are loss-free (reverse join through members.id_uuid
    back to the exact same members.id values). The table's own id is NOT
    loss-free, same caveat as every other Final-Cutover downgrade in this
    series: a freshly created integer sequence has no relationship to any
    UUID that may already have circulated. Emergency rollback shortly
    after deploy only, never a production path.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- Revert resolved_by ------------------------------------------------
    op.add_column(TABLE, sa.Column("resolved_by_int", sa.Integer(), nullable=True))
    _join_backfill("resolved_by", "resolved_by_int", to_uuid=False)
    op.drop_constraint(
        "member_change_requests_resolved_by_fkey", TABLE, type_="foreignkey"
    )
    op.drop_column(TABLE, "resolved_by")
    op.alter_column(TABLE, "resolved_by_int", new_column_name="resolved_by")
    op.create_foreign_key(
        "member_change_requests_resolved_by_fkey",
        TABLE,
        "members",
        ["resolved_by"],
        ["id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )

    # --- Revert member_id ----------------------------------------------
    op.add_column(TABLE, sa.Column("member_id_int", sa.Integer(), nullable=True))
    _join_backfill("member_id", "member_id_int", to_uuid=False)
    op.drop_constraint(
        "member_change_requests_member_id_fkey", TABLE, type_="foreignkey"
    )
    op.drop_column(TABLE, "member_id")
    op.alter_column(TABLE, "member_id_int", new_column_name="member_id")
    op.alter_column(TABLE, "member_id", nullable=False)
    op.create_index("ix_member_change_requests_member_id", TABLE, ["member_id"])
    op.execute(
        f"CREATE UNIQUE INDEX member_change_requests_member_pending_uniq "
        f"ON {TABLE} (member_id) WHERE status = 'pending'"
    )
    op.create_foreign_key(
        "member_change_requests_member_id_fkey",
        TABLE,
        "members",
        ["member_id"],
        ["id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )

    # --- Revert own primary key --------------------------------------
    op.add_column(TABLE, sa.Column("id_int", sa.Integer(), nullable=True))
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
