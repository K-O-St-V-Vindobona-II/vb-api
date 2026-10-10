"""public content: no blank or oversized texts, only the accepted image types

Revision ID: c3e5a7f9b1d2
Revises: 7a3e91c4d5f8
Create Date: 2026-10-09 22:00:00.000000

1. The texts of the public site (about tabs, programme hints, quotes) were only
   required to be longer than zero characters, so a value of blanks was stored
   and shown as an empty box, and the maximum lengths the API enforces were not
   in the database. Each check now requires at least one character that is not
   white space and the same maximum length as the API.
2. public_gallery_images.content_type and extension were free text although the
   upload accepts JPEG and PNG only. Both are checked.
3. p4x_special_contacts.cn is required: a special contact without a name cannot
   be told apart in the partner search.

All checks of existing data run first: when one fails, nothing is changed, no
lock is taken and the error lists the offending rows.
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e5a7f9b1d2"
down_revision: str | Sequence[str] | None = "7a3e91c4d5f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, constraint, column, maximum length of the API field)
_TEXT_CHECKS = (
    ("public_site_about_tabs", "public_site_about_tabs_title_check", "title", 100),
    ("public_site_about_tabs", "public_site_about_tabs_body_check", "body", 6000),
    (
        "public_site_programm_hints",
        "public_site_programm_hints_text_check",
        "text",
        300,
    ),
    ("public_site_quotes", "public_site_quotes_quote_check", "quote", 500),
    ("public_site_quotes", "public_site_quotes_author_check", "author", 100),
)
_GALLERY_CHECKS = (
    (
        "public_gallery_images_content_type_check",
        "content_type IN ('image/jpeg', 'image/png')",
    ),
    ("public_gallery_images_extension_check", "extension IN ('jpg', 'png')"),
)


def _text_condition(column: str, max_length: int) -> str:
    return f"{column} ~ '[^[:space:]]' AND char_length({column}) <= {max_length}"


def _ids(bind: sa.Connection, query: str) -> list[object]:
    return list(bind.execute(sa.text(query)).scalars().all())


def _refuse_bad_data(bind: sa.Connection) -> None:
    for table, _constraint, column, max_length in _TEXT_CHECKS:
        rows = _ids(
            bind,
            f"SELECT id FROM {table} "  # noqa: S608
            f"WHERE NOT ({_text_condition(column, max_length)})",
        )
        if rows:
            msg = (
                f"{table}.{column} is blank or longer than {max_length} in rows {rows}"
            )
            raise RuntimeError(msg)

    images = _ids(
        bind,
        "SELECT id FROM public_gallery_images WHERE NOT "
        "(content_type IN ('image/jpeg', 'image/png') "
        "AND extension IN ('jpg', 'png'))",
    )
    if images:
        msg = f"Gallery images with another type in rows {images}"
        raise RuntimeError(msg)

    unnamed = _ids(bind, "SELECT id FROM p4x_special_contacts WHERE cn IS NULL")
    if unnamed:
        msg = f"Special contacts without a name in rows {unnamed}"
        raise RuntimeError(msg)


def upgrade() -> None:
    """Upgrade schema."""
    _refuse_bad_data(op.get_bind())

    op.execute("SET LOCAL lock_timeout = '5s'")

    for table, constraint, column, max_length in _TEXT_CHECKS:
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(
            constraint, table, _text_condition(column, max_length)
        )
    for constraint, condition in _GALLERY_CHECKS:
        op.create_check_constraint(constraint, "public_gallery_images", condition)
    op.alter_column(
        "p4x_special_contacts", "cn", existing_type=sa.String(), nullable=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.alter_column(
        "p4x_special_contacts", "cn", existing_type=sa.String(), nullable=True
    )
    for constraint, _condition in _GALLERY_CHECKS:
        op.drop_constraint(constraint, "public_gallery_images", type_="check")
    for table, constraint, column, _max_length in _TEXT_CHECKS:
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, f"char_length({column}) > 0")
