"""Tests for app.services.storage_integrity_service."""

from datetime import UTC, datetime
from unittest.mock import patch

from app.models.archive_store_item import ArchiveStoreItem
from app.models.member import Member
from app.models.standesdb_image import StandesdbImage
from app.services.storage_integrity_service import (
    check_archive_integrity,
    check_standesdb_integrity,
)


def _make_archive_item(sha256_hash: str) -> ArchiveStoreItem:
    now = datetime.now(UTC)
    return ArchiveStoreItem(
        name="testfile",
        extension="pdf",
        mime_type="application/pdf",
        size=100,
        sha256_hash=sha256_hash,
        created_at=now,
        updated_at=now,
    )


class TestCheckArchiveIntegrity:
    def test_reports_missing_file(self, db_session, mock_s3):
        db_session.add(_make_archive_item("missing_hash"))
        db_session.commit()

        report = check_archive_integrity(db_session, mock_s3)

        assert report.missing == ["archive/store/missing_hash"]
        assert report.is_healthy is False

    def test_healthy_when_file_present(self, db_session, mock_s3):
        db_session.add(_make_archive_item("present_hash"))
        db_session.commit()
        mock_s3.upload("archive/store/present_hash", b"data")

        report = check_archive_integrity(db_session, mock_s3)

        assert report.missing == []
        assert report.is_healthy is True

    def test_reports_orphaned_object(self, db_session, mock_s3):
        mock_s3.upload("archive/store/orphan_hash", b"data")

        report = check_archive_integrity(db_session, mock_s3)

        # `in`, not `==`: the S3 bucket is shared across the whole test
        # session (moto backend), so other tests' uploads under this
        # prefix may still be present - only assert our own key surfaces.
        assert "archive/store/orphan_hash" in report.orphans
        # Orphans alone don't make the report unhealthy - only missing files do.
        assert report.is_healthy is True

    def test_never_calls_head_or_exists_per_row(self, db_session, mock_s3):
        """The whole point of the bulk-listing approach: checking must be a
        pure in-memory set comparison after a single list_keys() call, not
        one S3 API call per DB row (see test_check_s3_integrity.py history
        for the OOM/perf regression this guards against)."""
        for i in range(5):
            db_session.add(_make_archive_item(f"hash_{i}"))
        db_session.commit()

        with (
            patch.object(mock_s3, "exists") as mock_exists,
            patch.object(mock_s3, "head") as mock_head,
        ):
            check_archive_integrity(db_session, mock_s3)
            mock_exists.assert_not_called()
            mock_head.assert_not_called()


class TestCheckStandesdbIntegrity:
    def test_reports_missing_file(self, db_session, mock_s3):
        member = Member(vorname="Test", nachname="User")
        db_session.add(member)
        db_session.commit()

        db_session.add(
            StandesdbImage(owner_member_id=member.id, sha256_hash="missing_img")
        )
        db_session.commit()

        report = check_standesdb_integrity(db_session, mock_s3)

        assert report.missing == ["standesdb/images/missing_img"]
        assert report.is_healthy is False

    def test_healthy_when_file_present(self, db_session, mock_s3):
        member = Member(vorname="Test", nachname="User")
        db_session.add(member)
        db_session.commit()

        db_session.add(
            StandesdbImage(owner_member_id=member.id, sha256_hash="present_img")
        )
        db_session.commit()
        mock_s3.upload("standesdb/images/present_img", b"data")

        report = check_standesdb_integrity(db_session, mock_s3)

        assert report.missing == []
        assert report.is_healthy is True

    def test_reports_orphaned_object(self, db_session, mock_s3):
        mock_s3.upload("standesdb/images/orphan_img", b"data")

        report = check_standesdb_integrity(db_session, mock_s3)

        assert "standesdb/images/orphan_img" in report.orphans
