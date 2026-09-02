"""archive_permissions org_id/state_id fk

Revision ID: ce00727a65e4
Revises: e097221b6c5f
Create Date: 2026-09-02 20:30:00.000000

archive_permissions.org_id/state_id were plain TEXT columns with no
ForeignKey() at all, unlike every other org_id/state_id column in the
schema (e.g. members.org_id/state_id) - a genuinely missing FK, not a
UUID-typing gap, so it never blocked the schema-wide Integer-PK -> UUID
migration and was deferred until now. Zero orphaned rows verified before
adding the constraints (both columns only ever hold the fixed set of
seeded orgs/states ids). ON DELETE RESTRICT matches the convention
already used for org_id/state_id everywhere else in the schema (see
members.org_id/state_id) - a permission rule referencing an org/state
must be reassigned or removed before that org/state itself can be
deleted.
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ce00727a65e4"
down_revision: str | None = "e097221b6c5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_FK = "archive_permissions_org_id_fkey"
STATE_FK = "archive_permissions_state_id_fkey"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_foreign_key(
        ORG_FK,
        "archive_permissions",
        "orgs",
        ["org_id"],
        ["id"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )
    op.create_foreign_key(
        STATE_FK,
        "archive_permissions",
        "states",
        ["state_id"],
        ["id"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(STATE_FK, "archive_permissions", type_="foreignkey")
    op.drop_constraint(ORG_FK, "archive_permissions", type_="foreignkey")
