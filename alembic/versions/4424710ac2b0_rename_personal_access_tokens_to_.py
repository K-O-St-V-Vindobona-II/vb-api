"""rename personal_access_tokens to sessions

Revision ID: 4424710ac2b0
Revises: 323f0222d0b9
Create Date: 2026-08-01 23:21:58.486045

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4424710ac2b0"
down_revision: str | Sequence[str] | None = "323f0222d0b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # This table was originally a repurposed legacy "personal access token"
    # (long-lived, user-named API key) table, but every row it has ever
    # held is actually a login session: the surrounding code already calls
    # this concept "session" everywhere (create_user_session(),
    # _get_session_record(), refresh_session(), "Session expired" error
    # messages) - only the table/model/column names still carried the old
    # naming. This migration brings the schema in line with the code.
    op.execute("ALTER TABLE personal_access_tokens RENAME TO sessions")
    # "token" actually stores the JWT's jti claim (see
    # app/core/security.py's create_access_token: token_id = ... "jti":
    # token_id) - renamed to the precise, spec-matching name.
    op.execute("ALTER TABLE sessions RENAME COLUMN token TO jti")
    # "name" was always hardcoded to the literal "session" (the only
    # creation site is auth_service.create_user_session()) - never a real
    # per-client distinction, same dead-column pattern as
    # archive_store_items.original_name/backedup_at.
    op.drop_column("sessions", "name")

    op.execute(
        "ALTER TABLE sessions RENAME CONSTRAINT "
        "personal_access_tokens_pkey TO sessions_pkey"
    )
    op.execute(
        "ALTER TABLE sessions RENAME CONSTRAINT "
        "personal_access_tokens_member_id_fkey TO sessions_member_id_fkey"
    )
    op.execute("ALTER INDEX ix_personal_access_tokens_id RENAME TO ix_sessions_id")
    op.execute("ALTER INDEX ix_personal_access_tokens_token RENAME TO ix_sessions_jti")
    op.execute(
        "ALTER TRIGGER personal_access_tokens_set_updated_at "
        "ON sessions RENAME TO sessions_set_updated_at"
    )
    op.execute("ALTER SEQUENCE personal_access_tokens_id_seq RENAME TO sessions_id_seq")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.execute("ALTER SEQUENCE sessions_id_seq RENAME TO personal_access_tokens_id_seq")
    op.execute(
        "ALTER TRIGGER sessions_set_updated_at "
        "ON sessions RENAME TO personal_access_tokens_set_updated_at"
    )
    op.execute("ALTER INDEX ix_sessions_jti RENAME TO ix_personal_access_tokens_token")
    op.execute("ALTER INDEX ix_sessions_id RENAME TO ix_personal_access_tokens_id")
    op.execute(
        "ALTER TABLE sessions RENAME CONSTRAINT "
        "sessions_member_id_fkey TO personal_access_tokens_member_id_fkey"
    )
    op.execute(
        "ALTER TABLE sessions RENAME CONSTRAINT "
        "sessions_pkey TO personal_access_tokens_pkey"
    )

    op.add_column("sessions", sa.Column("name", sa.String(), nullable=True))
    op.execute("UPDATE sessions SET name = 'session'")
    op.execute("ALTER TABLE sessions RENAME COLUMN jti TO token")
    op.execute("ALTER TABLE sessions RENAME TO personal_access_tokens")
