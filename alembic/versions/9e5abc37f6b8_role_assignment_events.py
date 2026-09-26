"""role_assignment_events: append-only history of role grants and revocations

Revision ID: 9e5abc37f6b8
Revises: 8d4fab26e5a7
Create Date: 2026-09-21 12:00:00.000000

Roles carry permissions, and members with the standesdb admin permission edit
them. This table records every grant, revocation and period change with the
member who made it. It is append-only: a trigger rejects every UPDATE and
DELETE, and the foreign keys restrict deletion of the referenced members and
roles. The history starts with this migration; earlier changes stay in
members_logs (key roles_history).

updated_at exists for schema uniformity (it always equals created_at, because
the row is never updated).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9e5abc37f6b8"
down_revision: str | Sequence[str] | None = "8d4fab26e5a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    action = postgresql.ENUM(
        "granted", "revoked", "period_changed", name="role_assignment_action"
    )
    action.create(op.get_bind())

    op.create_table(
        "role_assignment_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.String(), nullable=False),
        sa.Column(
            "action",
            postgresql.ENUM(name="role_assignment_action", create_type=False),
            nullable=False,
        ),
        sa.Column("startdate", sa.Date(), nullable=False),
        sa.Column("enddate", sa.Date(), nullable=True),
        sa.Column("previous_enddate", sa.Date(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="role_assignment_events_pkey"),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["members.id"],
            name="role_assignment_events_member_id_fkey",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name="role_assignment_events_role_id_fkey",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["members.id"],
            name="role_assignment_events_actor_id_fkey",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "enddate IS NULL OR startdate < enddate",
            name="role_assignment_events_startdate_enddate_check",
        ),
    )
    for column in ("member_id", "role_id", "actor_id", "created_at"):
        op.create_index(
            f"ix_role_assignment_events_{column}", "role_assignment_events", [column]
        )

    op.execute(
        "CREATE TRIGGER role_assignment_events_set_updated_at "
        "BEFORE UPDATE ON role_assignment_events "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
    op.execute(
        """
        CREATE FUNCTION reject_role_assignment_event_change()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'role_assignment_events is append-only';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER role_assignment_events_append_only "
        "BEFORE UPDATE OR DELETE ON role_assignment_events "
        "FOR EACH ROW EXECUTE FUNCTION reject_role_assignment_event_change()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_table("role_assignment_events")
    op.execute("DROP FUNCTION reject_role_assignment_event_change()")
    postgresql.ENUM(name="role_assignment_action").drop(op.get_bind())
