"""request_logs fk cutover and client_user_agents final cutover

Revision ID: e186839a1e80
Revises: 267386fa75f3
Create Date: 2026-09-01 23:05:00.000000

request_logs.member_id and request_logs.client_user_agent_id were bare
integer columns with no ForeignKey() at all - genuinely missing FKs,
not a Referrer-Cutover of existing ones. member_id now references
members.id_uuid (members itself won't have a UUID primary key until
its own Final-Cutover); client_user_agent_id references
client_user_agents.id directly, since that table's own Final-Cutover
(it has no other referrer) is bundled into this same migration -
client_user_agents.id_uuid was never anything but its eventual primary
key, so there is no intermediate state where a FK to it would need to
point anywhere else.

Both backfills are explicitly chunked by id range rather than a single
UPDATE ... FROM statement (unlike every other Referrer-Cutover in this
series so far): request_logs is the fastest-growing table in the
schema (~27k rows and counting, only bounded by the scheduled cleanup
job's TRACKING_RETENTION_MONTHS window), so a single long-running
UPDATE risks a materially longer lock window on a table every request
writes to.

request_logs.id itself is deliberately untouched here - it's exposed
via GET /tracking/activity/{log_id} and stays an integer path param;
converting it is out of this slice's scope.
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e186839a1e80"
down_revision: str | Sequence[str] | None = "267386fa75f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "request_logs"
BATCH_SIZE = 5000


def _chunked_join_backfill(
    fk_col: str,
    tmp_col: str,
    parent_table: str,
    parent_key_col: str,
) -> None:
    """Copies parent_key_col into tmp_col for every row whose fk_col
    matches, one bounded id range of TABLE at a time."""
    bind = op.get_bind()
    min_id, max_id = bind.execute(
        sa.text(f"SELECT min(id), max(id) FROM {TABLE}")  # noqa: S608
    ).one()
    if min_id is None:
        return
    lo = min_id
    while lo <= max_id:
        hi = lo + BATCH_SIZE - 1
        bind.execute(
            sa.text(
                f"UPDATE {TABLE} t SET {tmp_col} = p.{parent_key_col} "  # noqa: S608
                f"FROM {parent_table} p "
                f"WHERE t.{fk_col} = p.id AND t.id BETWEEN :lo AND :hi"
            ),
            {"lo": lo, "hi": hi},
        )
        lo = hi + 1


def _chunked_join_revert(
    fk_col: str,
    tmp_col: str,
    parent_table: str,
    parent_uuid_col: str,
) -> None:
    """Reverse of _chunked_join_backfill: copies the parent's (new)
    integer id into tmp_col by matching on the parent's id_uuid."""
    bind = op.get_bind()
    min_id, max_id = bind.execute(
        sa.text(f"SELECT min(id), max(id) FROM {TABLE}")  # noqa: S608
    ).one()
    if min_id is None:
        return
    lo = min_id
    while lo <= max_id:
        hi = lo + BATCH_SIZE - 1
        bind.execute(
            sa.text(
                f"UPDATE {TABLE} t SET {tmp_col} = p.id "  # noqa: S608
                f"FROM {parent_table} p "
                f"WHERE t.{fk_col} = p.{parent_uuid_col} AND t.id BETWEEN :lo AND :hi"
            ),
            {"lo": lo, "hi": hi},
        )
        lo = hi + 1


def _cutover_client_user_agents_own_id() -> None:
    op.drop_constraint("client_user_agents_pkey", "client_user_agents", type_="primary")
    op.drop_column("client_user_agents", "id")
    op.execute("DROP SEQUENCE IF EXISTS client_user_agents_id_seq")
    op.alter_column("client_user_agents", "id_uuid", new_column_name="id")
    op.execute(
        "ALTER TABLE client_user_agents ADD CONSTRAINT client_user_agents_pkey "
        "PRIMARY KEY USING INDEX client_user_agents_id_uuid_key"
    )


def _revert_client_user_agents_own_id() -> None:
    """Reverts client_user_agents to its Phase-A-only shape (integer id
    as primary key, id_uuid alongside it) - not loss-free, a freshly
    created sequence has no relationship to any UUID that may already
    have circulated, same caveat as every other Final-Cutover downgrade
    in this series, emergency rollback only."""
    op.drop_constraint("client_user_agents_pkey", "client_user_agents", type_="primary")
    op.alter_column("client_user_agents", "id", new_column_name="id_uuid")
    op.add_column("client_user_agents", sa.Column("id", sa.Integer(), nullable=True))
    op.execute(
        "CREATE SEQUENCE client_user_agents_id_seq OWNED BY client_user_agents.id"
    )
    op.execute(
        "UPDATE client_user_agents SET id = nextval('client_user_agents_id_seq')"
    )
    op.alter_column(
        "client_user_agents",
        "id",
        nullable=False,
        server_default=sa.text("nextval('client_user_agents_id_seq'::regclass)"),
    )
    op.create_primary_key("client_user_agents_pkey", "client_user_agents", ["id"])
    op.create_index("ix_client_user_agents_id", "client_user_agents", ["id"])
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY client_user_agents_id_uuid_key "
            "ON client_user_agents (id_uuid)"
        )


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # 1. Backfill request_logs' two new FK columns via chunked joins,
    #    against the parents' id_uuid columns (both already exist and
    #    are fully populated from earlier Phase A slices).
    op.add_column(TABLE, sa.Column("member_id_uuid", sa.Uuid(), nullable=True))
    _chunked_join_backfill("member_id", "member_id_uuid", "members", "id_uuid")
    op.add_column(
        TABLE, sa.Column("client_user_agent_id_uuid", sa.Uuid(), nullable=True)
    )
    _chunked_join_backfill(
        "client_user_agent_id",
        "client_user_agent_id_uuid",
        "client_user_agents",
        "id_uuid",
    )

    # 2. Swap the old integer columns for their UUID replacements.
    op.drop_column(TABLE, "member_id")
    op.alter_column(TABLE, "member_id_uuid", new_column_name="member_id")
    op.drop_column(TABLE, "client_user_agent_id")
    op.alter_column(
        TABLE, "client_user_agent_id_uuid", new_column_name="client_user_agent_id"
    )

    # 3. client_user_agents' own Final-Cutover - only now, since step 1's
    #    backfill needed the pre-cutover id_uuid column to still exist
    #    under that name.
    _cutover_client_user_agents_own_id()

    # 4. Indexes + FKs on request_logs, now that both sides are in their
    #    final shape.
    op.create_index("ix_request_logs_member_id", TABLE, ["member_id"])
    op.create_foreign_key(
        "request_logs_member_id_fkey",
        TABLE,
        "members",
        ["member_id"],
        ["id_uuid"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )
    op.create_index(
        "ix_request_logs_client_user_agent_id", TABLE, ["client_user_agent_id"]
    )
    op.create_foreign_key(
        "request_logs_client_user_agent_id_fkey",
        TABLE,
        "client_user_agents",
        ["client_user_agent_id"],
        ["id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def downgrade() -> None:
    """Downgrade schema.

    Every FK half here is loss-free (the reverse join maps back through
    the parent's id_uuid to the exact same integer values that were
    there before). client_user_agents' own id is NOT loss-free - see
    _revert_client_user_agents_own_id()'s docstring.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.drop_constraint(
        "request_logs_client_user_agent_id_fkey", TABLE, type_="foreignkey"
    )
    op.drop_index("ix_request_logs_client_user_agent_id", table_name=TABLE)
    op.drop_constraint("request_logs_member_id_fkey", TABLE, type_="foreignkey")
    op.drop_index("ix_request_logs_member_id", table_name=TABLE)

    _revert_client_user_agents_own_id()

    op.add_column(TABLE, sa.Column("member_id_int", sa.Integer(), nullable=True))
    _chunked_join_revert("member_id", "member_id_int", "members", "id_uuid")
    op.drop_column(TABLE, "member_id")
    op.alter_column(TABLE, "member_id_int", new_column_name="member_id")

    op.add_column(
        TABLE, sa.Column("client_user_agent_id_int", sa.Integer(), nullable=True)
    )
    _chunked_join_revert(
        "client_user_agent_id",
        "client_user_agent_id_int",
        "client_user_agents",
        "id_uuid",
    )
    op.drop_column(TABLE, "client_user_agent_id")
    op.alter_column(
        TABLE, "client_user_agent_id_int", new_column_name="client_user_agent_id"
    )
