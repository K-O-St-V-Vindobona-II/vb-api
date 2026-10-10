"""Filters for Alembic's comparison of the models with the database."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.schema import SchemaItem


def include_object(
    _obj: SchemaItem,
    _name: str | None,
    type_: str,
    reflected: bool,  # noqa: FBT001 (Alembic calls the hook positionally)
    compare_to: SchemaItem | None,
) -> bool:
    """Skip indexes that exist only in the database.

    The trigram, full-text and soft-delete indexes are created in migrations
    and not declared on the models. Without this filter
    `alembic revision --autogenerate` proposes to drop every one of them.
    """
    return not (type_ == "index" and reflected and compare_to is None)
