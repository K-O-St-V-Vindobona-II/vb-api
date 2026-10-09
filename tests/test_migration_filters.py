"""Which indexes Alembic's comparison of the models with the database looks at."""

import pytest
from sqlalchemy import Column, Index, Integer, MetaData, Table

from app.db.migration_filters import include_object

_TABLE = Table("example", MetaData(), Column("id", Integer))
_INDEX = Index("ix_example_id", _TABLE.c.id)


@pytest.mark.parametrize(
    ("type_", "reflected", "compare_to", "expected"),
    [
        # exists only in the database (trigram, full-text, soft-delete indexes)
        ("index", True, None, False),
        # declared on the model and present in the database
        ("index", True, _INDEX, True),
        # declared on the model and missing from the database: must be reported
        ("index", False, None, True),
        ("index", False, _INDEX, True),
        # everything that is not an index is compared as before
        ("table", True, None, True),
        ("column", True, None, True),
        ("unique_constraint", True, None, True),
    ],
)
def test_only_database_only_indexes_are_skipped(type_, reflected, compare_to, expected):
    result = include_object(_INDEX, "ix_example_id", type_, reflected, compare_to)

    assert result is expected
