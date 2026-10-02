"""The schema built by the migration chain contains only valid indexes and constraints.

Some revisions build indexes CONCURRENTLY and validate CHECK constraints in a
separate step. A failure in either step leaves an INVALID index or a NOT VALID
constraint behind, which the planner ignores or which does not protect the data.
"""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_SEARCH_INDEXES = (
    "ix_members_search_vector",
    "ix_contacts_search_vector",
    "ix_archive_dirs_name_trgm",
    "ix_archive_dirs_description_trgm",
    "ix_archive_store_items_name_trgm",
    "ix_archive_store_items_extension_trgm",
    "ix_archive_files_description_trgm",
    "ix_archive_file_comments_content_trgm",
)


def test_no_index_is_invalid(db_session: Session) -> None:
    invalid = (
        db_session.execute(
            text(
                "SELECT c.relname FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE NOT i.indisvalid ORDER BY c.relname"
            )
        )
        .scalars()
        .all()
    )

    assert invalid == []


def test_every_check_constraint_is_validated(db_session: Session) -> None:
    unvalidated = (
        db_session.execute(
            text(
                "SELECT conrelid::regclass::text || '.' || conname FROM pg_constraint "
                "WHERE contype = 'c' AND NOT convalidated ORDER BY 1"
            )
        )
        .scalars()
        .all()
    )

    assert unvalidated == []


@pytest.mark.parametrize("index_name", _SEARCH_INDEXES)
def test_search_index_exists_and_is_valid(db_session: Session, index_name: str) -> None:
    valid = db_session.execute(
        text(
            "SELECT i.indisvalid FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
        ),
        {"name": index_name},
    ).scalar_one_or_none()

    assert valid is True
