"""archive comments and badges: required fields, description length

Revision ID: d8f2a4c6e0b1
Revises: c3e5a7f9b1d2
Create Date: 2026-10-10 09:00:00.000000

Fields that every write path requires were optional in the database:

1. archive_file_comments.content may no longer be NULL, and the check now holds
   the same 5 to 1000 characters the API enforces (it allowed 1 to 1000).
2. badges.name may no longer be NULL.
3. The descriptions of directories and files are limited to the 128 characters
   the API enforces.

All checks of existing data run first: when one fails, nothing is changed, no
lock is taken and the error lists the offending rows.
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8f2a4c6e0b1"
down_revision: str | Sequence[str] | None = "c3e5a7f9b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMENT_CHECK = "archive_file_comments_content_check"
_DESCRIPTION_CHECKS = (
    ("archive_dirs", "archive_dirs_description_check"),
    ("archive_files", "archive_files_description_check"),
)
_DESCRIPTION_CONDITION = "description IS NULL OR char_length(description) <= 128"


def _ids(bind: sa.Connection, query: str) -> list[object]:
    return list(bind.execute(sa.text(query)).scalars().all())


def _refuse_bad_data(bind: sa.Connection) -> None:
    problems = (
        (
            "Comments without text",
            "SELECT id FROM archive_file_comments WHERE content IS NULL",
        ),
        (
            "Comments shorter than 5 characters",
            "SELECT id FROM archive_file_comments WHERE char_length(content) < 5",
        ),
        ("Badges without a name", "SELECT id FROM badges WHERE name IS NULL"),
        (
            "Directories with a description over 128 characters",
            "SELECT id FROM archive_dirs WHERE char_length(description) > 128",
        ),
        (
            "Files with a description over 128 characters",
            "SELECT id FROM archive_files WHERE char_length(description) > 128",
        ),
    )
    for what, query in problems:
        rows = _ids(bind, query)
        if rows:
            msg = f"{what} in rows {rows}"
            raise RuntimeError(msg)


def upgrade() -> None:
    """Upgrade schema."""
    _refuse_bad_data(op.get_bind())

    op.execute("SET LOCAL lock_timeout = '5s'")

    op.drop_constraint(_COMMENT_CHECK, "archive_file_comments", type_="check")
    op.create_check_constraint(
        _COMMENT_CHECK,
        "archive_file_comments",
        "char_length(content) BETWEEN 5 AND 1000",
    )
    op.alter_column(
        "archive_file_comments", "content", existing_type=sa.String(), nullable=False
    )
    op.alter_column("badges", "name", existing_type=sa.String(), nullable=False)
    for table, constraint in _DESCRIPTION_CHECKS:
        op.create_check_constraint(constraint, table, _DESCRIPTION_CONDITION)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    for table, constraint in _DESCRIPTION_CHECKS:
        op.drop_constraint(constraint, table, type_="check")
    op.alter_column("badges", "name", existing_type=sa.String(), nullable=True)
    op.alter_column(
        "archive_file_comments", "content", existing_type=sa.String(), nullable=True
    )
    op.drop_constraint(_COMMENT_CHECK, "archive_file_comments", type_="check")
    op.create_check_constraint(
        _COMMENT_CHECK,
        "archive_file_comments",
        "content IS NULL OR length(content) BETWEEN 1 AND 1000",
    )
