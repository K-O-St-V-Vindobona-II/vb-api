"""request_logs id to uuid and jsonb payload columns

Revision ID: 8f0230673ae0
Revises: d1700831c357
Create Date: 2026-09-16 13:43:10.921915

request_logs.id was the last remaining integer primary key in the schema -
deliberately left out of the earlier schema-wide UUID-PK migration since it
was, at the time, still exposed as an integer path parameter. Closed here
now that the activity-log feature backing that endpoint is being rewritten
from scratch anyway. No other table has a foreign key onto request_logs.id
(it is a leaf/audit table), so - unlike most tables in the earlier
UUID-PK series - this needs no separate Phase A / Final-Cutover split and
no Referrer-Cutover slice elsewhere.

client_ips/request_input/response_content also move from TEXT (holding
manually json.dumps()-encoded text) to native JSONB. client_ips carries two
historical shapes in production data: a JSON-array-literal string (current
format) and a bare, unbracketed single IP (older format, no comma-separated
multi-IP values found) - both are normalized into a proper JSONB array.
request_input/response_content are already valid JSON text in every
existing row (verified against production data before writing this
migration) and are cast directly.
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f0230673ae0"
down_revision: str | Sequence[str] | None = "d1700831c357"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "request_logs"
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
                sa.text(
                    f"UPDATE {TABLE} SET id_uuid = :new_id WHERE id = :old_id"  # noqa: S608
                ),
                {"new_id": uuid.uuid7(), "old_id": row.id},
            )


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # --- id: int -> UUID (single-shot, no referrer table to wait for) -----
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

    # --- client_ips/request_input/response_content: TEXT -> JSONB ---------
    op.execute(
        f"ALTER TABLE {TABLE} ALTER COLUMN client_ips TYPE jsonb USING "
        "(CASE "
        "WHEN client_ips IS NULL THEN NULL "
        "WHEN client_ips LIKE '[%' THEN client_ips::jsonb "
        "ELSE to_jsonb(ARRAY[client_ips]) "
        "END)"
    )
    op.alter_column(TABLE, "client_ips", nullable=False)
    op.execute(
        f"ALTER TABLE {TABLE} ALTER COLUMN request_input TYPE jsonb "
        "USING request_input::jsonb"
    )
    op.execute(
        f"ALTER TABLE {TABLE} ALTER COLUMN response_content TYPE jsonb "
        "USING response_content::jsonb"
    )

    # --- created_at: DB-managed default from here on, NOT NULL enforced ---
    # (no existing row has a NULL created_at - verified against production
    # data before writing this migration - and the new default guarantees
    # every future row gets one too).
    op.execute(f"ALTER TABLE {TABLE} ALTER COLUMN created_at SET DEFAULT now()")
    op.alter_column(TABLE, "created_at", nullable=False)


def downgrade() -> None:
    """Downgrade schema.

    Not loss-free for `id` - a freshly created sequence has no relationship
    to any UUID that may already have circulated, same caveat as every
    other Final-Cutover downgrade in this migration series, emergency
    rollback only. The JSONB -> TEXT reversal is loss-free (jsonb's own
    text representation round-trips through json.loads() the same way the
    original json.dumps() output did).
    """
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.alter_column(TABLE, "created_at", nullable=True)
    op.execute(f"ALTER TABLE {TABLE} ALTER COLUMN created_at DROP DEFAULT")

    op.execute(
        f"ALTER TABLE {TABLE} ALTER COLUMN response_content TYPE varchar "
        "USING response_content::text"
    )
    op.execute(
        f"ALTER TABLE {TABLE} ALTER COLUMN request_input TYPE varchar "
        "USING request_input::text"
    )
    op.alter_column(TABLE, "client_ips", nullable=True)
    op.execute(
        f"ALTER TABLE {TABLE} ALTER COLUMN client_ips TYPE varchar "
        "USING client_ips::text"
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
    op.drop_column(TABLE, "id_uuid")
