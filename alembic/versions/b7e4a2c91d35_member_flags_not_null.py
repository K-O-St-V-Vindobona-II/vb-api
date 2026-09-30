"""members: status flags are NOT NULL with database defaults

Revision ID: b7e4a2c91d35
Revises: 9e5abc37f6b8
Create Date: 2026-09-30 21:00:00.000000

gruender, entlassen, verstorben, chroniclemail and auth_locked were nullable
without a database default: only the ORM supplied one. A row created outside
the ORM (a restore, a manual insert, a script) therefore had NULL. The sign-in
checks read NULL as "not locked", and every list, count and export that
filters with entlassen = false or verstorben = false skipped the row.

The flags become NOT NULL with defaults (false, and true for auth_locked).
Existing NULLs are set to the same defaults first, so an account that was open
only by omission ends up locked. Rows with an explicit value keep it.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e4a2c91d35"
down_revision: str | Sequence[str] | None = "9e5abc37f6b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (column, SQL default)
_FLAGS: tuple[tuple[str, str], ...] = (
    ("gruender", "false"),
    ("entlassen", "false"),
    ("verstorben", "false"),
    ("chroniclemail", "false"),
    ("auth_locked", "true"),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    for column, default in _FLAGS:
        op.execute(
            f"UPDATE members SET {column} = {default} WHERE {column} IS NULL"  # noqa: S608
        )
        op.alter_column(
            "members",
            column,
            existing_type=sa.Boolean(),
            server_default=sa.text(default),
            nullable=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    for column, _default in _FLAGS:
        op.alter_column(
            "members",
            column,
            existing_type=sa.Boolean(),
            server_default=None,
            nullable=True,
        )
