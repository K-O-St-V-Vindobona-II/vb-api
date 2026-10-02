"""sessions NOT NULL timestamps; password_reset_tokens bound to the member

Revision ID: 7c3e9a15d4f6
Revises: 6b2d8f04c3e5
Create Date: 2026-09-21 09:30:00.000000

sessions: the idle timeout and the absolute lifetime skip their comparison when
last_used_at / created_at is NULL, so a row without them never expired. Both
columns (and updated_at) become NOT NULL with a server default; existing NULLs
are backfilled from the other timestamp, which makes such a row expire like any
other.

password_reset_tokens: the table was keyed by the e-mail address as text,
without a foreign key, so tokens of deleted members stayed behind and
created_at could be NULL (a row the purge job never removed). It is rebuilt
with a UUID primary key, a foreign key to members, one row per member,
`token_hash` (the column holds a digest), and NOT NULL timestamps with the
updated_at trigger. Pending tokens are worthless after 20 minutes, so the rows
are not carried over. A trigger on members deletes a member's token when the
e-mail address changes: a token that was mailed to the old address must not
reset the password once the address belongs to someone else.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c3e9a15d4f6"
down_revision: str | Sequence[str] | None = "6b2d8f04c3e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SESSION_COLUMNS = ("last_used_at", "created_at", "updated_at")


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    # Backfill first; the updated_at trigger stamps every touched row.
    op.execute(
        """
        UPDATE sessions
        SET created_at = COALESCE(created_at, last_used_at, now()),
            last_used_at = COALESCE(last_used_at, created_at, now())
        WHERE created_at IS NULL OR last_used_at IS NULL OR updated_at IS NULL
        """
    )
    for column in _SESSION_COLUMNS:
        op.alter_column(
            "sessions", column, nullable=False, server_default=sa.text("now()")
        )

    op.drop_table("password_reset_tokens")
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="password_reset_tokens_pkey"),
        sa.UniqueConstraint("member_id", name="password_reset_tokens_member_id_key"),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["members.id"],
            name="password_reset_tokens_member_id_fkey",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
    )
    op.execute(
        "CREATE TRIGGER password_reset_tokens_set_updated_at "
        "BEFORE UPDATE ON password_reset_tokens "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
    op.execute(
        """
        CREATE FUNCTION delete_password_reset_token_on_email_change()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            DELETE FROM password_reset_tokens WHERE member_id = NEW.id;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER members_email_change_delete_reset_token "
        "AFTER UPDATE OF email ON members "
        "FOR EACH ROW WHEN (OLD.email IS DISTINCT FROM NEW.email) "
        "EXECUTE FUNCTION delete_password_reset_token_on_email_change()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP TRIGGER members_email_change_delete_reset_token ON members")
    op.execute("DROP FUNCTION delete_password_reset_token_on_email_change()")
    op.drop_table("password_reset_tokens")
    op.create_table(
        "password_reset_tokens",
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("email", name="password_reset_tokens_pkey"),
    )
    for column in _SESSION_COLUMNS:
        op.alter_column("sessions", column, nullable=True, server_default=None)
