"""Regression test for scripts/check_s3_integrity.py model registration."""

import importlib

from sqlalchemy.orm import configure_mappers


def test_module_import_configures_mappers_without_error() -> None:
    """Importing the module must register all models (e.g. via
    app.db.base) so that relationships like ArchiveStoreItem.created_by
    -> Member can be resolved. Without that import, SQLAlchemy raises
    InvalidRequestError as soon as a query touching the relationship is
    configured.
    """
    importlib.import_module("scripts.check_s3_integrity")

    configure_mappers()
