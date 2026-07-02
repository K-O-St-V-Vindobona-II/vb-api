"""Regression tests for scripts/check_s3_integrity.py."""

from unittest.mock import patch

import scripts.check_s3_integrity as check_s3_integrity
from tests.scripts._subprocess_helpers import (
    assert_module_imports_and_configures_mappers,
)


def test_standalone_import_configures_mappers_without_error() -> None:
    """Run as a fresh process (not sharing pytest's conftest-populated
    SQLAlchemy registry) — a plain in-process import can't detect a
    missing `import app.db.base`, since conftest.py already registers
    every model for the whole test session before this test body even
    runs. Relationships like ArchiveStoreItem.created_by -> Member would
    otherwise only fail in real standalone execution
    (`python scripts/check_s3_integrity.py`), not under pytest.
    """
    assert_module_imports_and_configures_mappers("scripts.check_s3_integrity")


def test_get_s3_client_defaults_to_none_endpoint_when_unset(monkeypatch) -> None:
    """A hardcoded localhost:9000 fallback would make this script try to
    hit a local MinIO instead of real AWS S3 in production, where
    S3_ENDPOINT_URL is intentionally left unset."""
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    with patch.object(check_s3_integrity.boto3, "client") as mock_client:
        check_s3_integrity.get_s3_client()
        assert mock_client.call_args.kwargs["endpoint_url"] is None
