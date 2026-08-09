"""add member change requests

Revision ID: 757b97f0fae4
Revises: 85e63a22e9a7
Create Date: 2026-08-06 21:47:07.419701

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "757b97f0fae4"
down_revision: str | Sequence[str] | None = "85e63a22e9a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

member_change_request_status_enum = postgresql.ENUM(
    "pending",
    "resolved",
    name="member_change_request_status",
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    member_change_request_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "member_change_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            member_change_request_status_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("proposed_data", postgresql.JSONB(), nullable=False),
        sa.Column("field_decisions", postgresql.JSONB(), nullable=True),
        sa.Column("resolved_by", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["members.id"],
            name="member_change_requests_member_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"],
            ["members.id"],
            name="member_change_requests_resolved_by_fkey",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND field_decisions IS NULL"
            " AND resolved_at IS NULL)"
            " OR (status = 'resolved' AND field_decisions IS NOT NULL"
            " AND resolved_at IS NOT NULL)",
            name="member_change_requests_resolution_consistency_check",
        ),
        sa.CheckConstraint(
            "proposed_data <> '{}'::jsonb",
            name="member_change_requests_proposed_data_not_empty_check",
        ),
    )
    op.create_index(
        op.f("ix_member_change_requests_member_id"),
        "member_change_requests",
        ["member_id"],
    )
    # Enforces "at most one open request per member" - same partial-unique-
    # index pattern as p4x_category_directs_tx_category_active_uniq. Also
    # serves the org-scoped admin list query (WHERE status='pending' JOIN
    # members) efficiently.
    op.create_index(
        "member_change_requests_member_pending_uniq",
        "member_change_requests",
        ["member_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.execute(
        "CREATE TRIGGER member_change_requests_set_updated_at "
        "BEFORE UPDATE ON member_change_requests "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.execute(
        "DROP TRIGGER IF EXISTS member_change_requests_set_updated_at "
        "ON member_change_requests"
    )
    op.drop_index(
        "member_change_requests_member_pending_uniq",
        table_name="member_change_requests",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index(
        op.f("ix_member_change_requests_member_id"),
        table_name="member_change_requests",
    )
    op.drop_table("member_change_requests")

    member_change_request_status_enum.drop(op.get_bind(), checkfirst=True)
