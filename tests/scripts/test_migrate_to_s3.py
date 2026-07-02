"""Regression tests for scripts/migrate_to_s3.py."""

from unittest.mock import patch

import scripts.migrate_to_s3 as migrate_to_s3
from tests.scripts._subprocess_helpers import (
    assert_module_imports_and_configures_mappers,
)


def test_standalone_import_configures_mappers_without_error() -> None:
    """Run as a fresh process (not sharing pytest's conftest-populated
    SQLAlchemy registry) — this is what actually catches a missing
    `import app.db.base`, which crashes real standalone execution
    (`python scripts/migrate_to_s3.py`) with `InvalidRequestError:
    ... failed to locate a name ('Member')` on the ArchiveStoreItem ->
    Member relationship, since StandesdbImage/ArchiveStoreItem are queried
    but Member is never otherwise imported by this script."""
    assert_module_imports_and_configures_mappers("scripts.migrate_to_s3")


def test_get_s3_client_defaults_to_none_endpoint_when_unset(monkeypatch) -> None:
    """A hardcoded localhost:9000 fallback would make this script try to
    hit a local MinIO instead of real AWS S3 in production, where
    S3_ENDPOINT_URL is intentionally left unset."""
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    with patch.object(migrate_to_s3.boto3, "client") as mock_client:
        migrate_to_s3.get_s3_client()
        assert mock_client.call_args.kwargs["endpoint_url"] is None


def test_get_s3_client_uses_s3_region_env_var(monkeypatch) -> None:
    """region_name must come from S3_REGION, not be hardcoded to
    us-east-1 — a hardcoded region causes a SigV4 region mismatch against
    buckets in other regions (e.g. the real prod bucket in eu-central-1)."""
    monkeypatch.setenv("S3_REGION", "eu-central-1")
    with patch.object(migrate_to_s3.boto3, "client") as mock_client:
        migrate_to_s3.get_s3_client()
        assert mock_client.call_args.kwargs["region_name"] == "eu-central-1"


def test_get_s3_client_region_defaults_to_us_east_1(monkeypatch) -> None:
    monkeypatch.delenv("S3_REGION", raising=False)
    with patch.object(migrate_to_s3.boto3, "client") as mock_client:
        migrate_to_s3.get_s3_client()
        assert mock_client.call_args.kwargs["region_name"] == "us-east-1"
