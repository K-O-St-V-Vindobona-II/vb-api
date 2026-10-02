"""members_oauth2bindings: unique identities, closed provider set, NOT NULL times

Revision ID: 6b2d8f04c3e5
Revises: 5a1c7e93b2d4
Create Date: 2026-09-21 09:20:00.000000

Google sign-in resolves a member through this table, so it must be a function:
one external identity belongs to one member, and a member has one identity per
provider. Until now only the application checked that (check, then insert),
and two concurrent link requests could bind the same Google account to two
members. The provider becomes a native ENUM instead of free text, and the two
timestamps become NOT NULL with a server default.

The migration stops with a clear message if duplicates already exist; resolve
them first (the query in the guard lists them).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6b2d8f04c3e5"
down_revision: str | Sequence[str] | None = "5a1c7e93b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "members_oauth2bindings"
_PROVIDER_UNIQUE = "members_oauth2bindings_provider_remote_id_key"
_MEMBER_UNIQUE = "members_oauth2bindings_member_id_provider_key"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM members_oauth2bindings
                GROUP BY provider, remote_id HAVING count(*) > 1
            ) OR EXISTS (
                SELECT 1 FROM members_oauth2bindings
                GROUP BY member_id, provider HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'members_oauth2bindings holds duplicate identities: resolve '
                    'them first (GROUP BY provider, remote_id / member_id, provider)';
            END IF;
        END $$;
        """
    )

    postgresql.ENUM("google", name="oauth_provider").create(op.get_bind())
    op.execute(
        "ALTER TABLE members_oauth2bindings ALTER COLUMN provider "
        "TYPE oauth_provider USING provider::oauth_provider"
    )
    op.create_unique_constraint(_PROVIDER_UNIQUE, _TABLE, ["provider", "remote_id"])
    op.create_unique_constraint(_MEMBER_UNIQUE, _TABLE, ["member_id", "provider"])
    # The second constraint's leading column serves every lookup by member.
    op.drop_index("ix_members_oauth2bindings_member_id", table_name=_TABLE)

    op.execute(
        """
        UPDATE members_oauth2bindings
        SET bound_at = COALESCE(bound_at, now()),
            lastuse_at = COALESCE(lastuse_at, bound_at, now())
        WHERE bound_at IS NULL OR lastuse_at IS NULL
        """
    )
    for column in ("bound_at", "lastuse_at"):
        op.alter_column(_TABLE, column, nullable=False, server_default=sa.text("now()"))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for column in ("bound_at", "lastuse_at"):
        op.alter_column(_TABLE, column, nullable=True, server_default=None)
    op.create_index("ix_members_oauth2bindings_member_id", _TABLE, ["member_id"])
    op.drop_constraint(_MEMBER_UNIQUE, _TABLE, type_="unique")
    op.drop_constraint(_PROVIDER_UNIQUE, _TABLE, type_="unique")
    op.execute(
        "ALTER TABLE members_oauth2bindings ALTER COLUMN provider "
        "TYPE VARCHAR USING provider::text"
    )
    postgresql.ENUM("google", name="oauth_provider").drop(op.get_bind())
