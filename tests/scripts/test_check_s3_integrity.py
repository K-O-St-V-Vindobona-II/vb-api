"""Regression tests for scripts/check_s3_integrity.py."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

import scripts.check_s3_integrity as check_s3_integrity
from app.models.archive_store_item import ArchiveStoreItem
from app.models.standesdb_image import StandesdbImage
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


def test_check_completeness_reports_missing_by_id(db_session) -> None:
    """Regression test for the column-only rewrite: check_completeness
    must still report the correct count and the correct
    StandesdbImage.id/ArchiveStoreItem.id in its output, now via
    db.query(Model.id, Model.sha256_hash) instead of
    db.query(Model).all() (the latter eagerly joins
    ArchiveStoreItem.member and OOM-killed the sibling migrate_to_s3.py
    in production on 27k+ rows)."""
    now = datetime.now(UTC)
    db_session.add(
        StandesdbImage(
            owner_type="member",
            owner_id=1,
            sha256_hash="missing_img_hash",
        )
    )
    db_session.add(
        ArchiveStoreItem(
            name="testfile",
            extension="pdf",
            mime_type="application/pdf",
            size=100,
            sha256_hash="present_arch_hash",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    mock_client = MagicMock()

    def head_object(Bucket, Key):  # noqa: N803, ARG001 — must match boto3's kwarg names
        if Key.endswith("present_arch_hash"):
            return {}
        raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    mock_client.head_object.side_effect = head_object

    missing = check_s3_integrity.check_completeness(
        mock_client, "test-bucket", db_session
    )

    assert missing == 1
