"""Regression tests for scripts/check_s3_integrity.py."""

from datetime import UTC, datetime
from unittest.mock import patch

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


def test_check_completeness_reports_missing_by_id(db_session, capsys) -> None:
    """Regression test for the column-only + bulk-listing rewrite:
    check_completeness must still report the correct count and the
    correct StandesdbImage.id/ArchiveStoreItem.id in its output — now via
    db.query(Model.id, Model.sha256_hash) plus a set-membership check
    against a pre-fetched S3 key set, instead of one head_object() call
    per DB row. The per-row HEAD approach took tens of minutes over the
    network for ~27k rows in production; the ArchiveStoreItem variant also
    eagerly joined ArchiveStoreItem.member (lazy="joined") and OOM-killed
    the sibling migrate_to_s3.py, which had the same query pattern."""
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

    s3_standesdb: set[str] = set()
    s3_archive = {f"{check_s3_integrity.ARCHIVE_PREFIX}/present_arch_hash"}

    missing = check_s3_integrity.check_completeness(
        db_session, s3_standesdb, s3_archive
    )

    assert missing == 1
    assert "MISSING:" in capsys.readouterr().out


def test_check_completeness_never_calls_head_object(db_session) -> None:
    """The whole point of the bulk-listing rewrite: completeness checking
    must be a pure in-memory set comparison, issuing zero S3 API calls
    per DB row."""
    db_session.add(StandesdbImage(owner_type="member", owner_id=1, sha256_hash="hash1"))
    db_session.commit()

    with patch.object(check_s3_integrity, "list_prefix") as mock_list_prefix:
        check_s3_integrity.check_completeness(db_session, set(), set())
        mock_list_prefix.assert_not_called()
