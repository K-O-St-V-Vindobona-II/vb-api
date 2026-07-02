"""Regression tests for scripts/check_s3_integrity.py."""

import importlib
from unittest.mock import patch

from sqlalchemy.orm import configure_mappers

import scripts.check_s3_integrity as check_s3_integrity


def test_module_import_configures_mappers_without_error() -> None:
    """Importing the module must register all models (e.g. via
    app.db.base) so that relationships like ArchiveStoreItem.created_by
    -> Member can be resolved. Without that import, SQLAlchemy raises
    InvalidRequestError as soon as a query touching the relationship is
    configured.
    """
    importlib.import_module("scripts.check_s3_integrity")

    configure_mappers()


def test_get_s3_client_defaults_to_none_endpoint_when_unset(monkeypatch) -> None:
    """A hardcoded localhost:9000 fallback would make this script try to
    hit a local MinIO instead of real AWS S3 in production, where
    S3_ENDPOINT_URL is intentionally left unset."""
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    with patch.object(check_s3_integrity.boto3, "client") as mock_client:
        check_s3_integrity.get_s3_client()
        assert mock_client.call_args.kwargs["endpoint_url"] is None
