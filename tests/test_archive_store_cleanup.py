"""Tests for archive_store_cleanup.py — the shared hard-delete of an archive
file row and the stored content nobody else references any more.
"""

from datetime import UTC, datetime
from unittest.mock import patch

from botocore.exceptions import ClientError

from app.core.storage import S3_PATH_ARCHIVE_CACHE, S3_PATH_ARCHIVE_STORE
from app.models.archive_file import ArchiveFile
from app.models.archive_store_item import ArchiveStoreItem
from app.services.archive_store_cleanup import hard_delete_file


def _make_item(db, suffix: str) -> ArchiveStoreItem:
    now = datetime.now(UTC)
    item = ArchiveStoreItem(
        name="testfile",
        extension="jpg",
        mime_type="image/jpeg",
        size=5000,
        sha256_hash=f"cleanup_{suffix}",
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    db.flush()
    return item


def _make_file(db, item: ArchiveStoreItem) -> ArchiveFile:
    now = datetime.now(UTC)
    file_obj = ArchiveFile(
        description="test",
        archive_store_item_id=item.id,
        created_at=now,
        updated_at=now,
    )
    db.add(file_obj)
    db.commit()
    return file_obj


def _client_error() -> ClientError:
    return ClientError({"Error": {"Code": "500", "Message": "fail"}}, "Operation")


class TestHardDeleteFile:
    def test_removes_row_item_content_and_every_cached_variant(
        self, db_session, mock_s3
    ):
        item = _make_item(db_session, "all")
        file_obj = _make_file(db_session, item)
        file_id, item_id = file_obj.id, item.id
        main_key = f"{S3_PATH_ARCHIVE_STORE}/{item.sha256_hash}"
        cache_keys = [
            f"{S3_PATH_ARCHIVE_CACHE}/{item.sha256_hash}.v2.thumb_sm",
            f"{S3_PATH_ARCHIVE_CACHE}/{item.sha256_hash}.v9.thumb_lg",
        ]
        mock_s3.upload(main_key, b"content", "image/jpeg")
        for key in cache_keys:
            mock_s3.upload(key, b"thumb", "image/jpeg")

        outcome = hard_delete_file(db_session, mock_s3, file_obj)

        db_session.expire_all()
        assert db_session.get(ArchiveFile, file_id) is None
        assert db_session.get(ArchiveStoreItem, item_id) is None
        assert outcome.store_item_deleted is True
        assert sorted(outcome.s3_keys_deleted) == sorted([main_key, *cache_keys])
        assert outcome.s3_errors == []
        assert not mock_s3.exists(main_key)

    def test_keeps_content_that_another_file_still_references(
        self, db_session, mock_s3
    ):
        item = _make_item(db_session, "shared")
        file_obj = _make_file(db_session, item)
        sibling = _make_file(db_session, item)
        main_key = f"{S3_PATH_ARCHIVE_STORE}/{item.sha256_hash}"
        mock_s3.upload(main_key, b"content", "image/jpeg")

        outcome = hard_delete_file(db_session, mock_s3, file_obj)

        assert db_session.get(ArchiveFile, sibling.id) is not None
        assert db_session.get(ArchiveStoreItem, item.id) is not None
        assert outcome.store_item_deleted is False
        assert outcome.s3_keys_deleted == []
        assert mock_s3.exists(main_key)

    def test_a_failing_cache_listing_is_reported_and_does_not_stop_the_rest(
        self, db_session, mock_s3
    ):
        item = _make_item(db_session, "listing")
        file_obj = _make_file(db_session, item)
        main_key = f"{S3_PATH_ARCHIVE_STORE}/{item.sha256_hash}"
        mock_s3.upload(main_key, b"content", "image/jpeg")

        with patch.object(mock_s3, "list_keys", side_effect=_client_error()):
            outcome = hard_delete_file(db_session, mock_s3, file_obj)

        assert outcome.s3_keys_deleted == [main_key]
        assert len(outcome.s3_errors) == 1
        assert outcome.s3_errors[0].startswith("list_keys(")

    def test_the_database_is_committed_before_storage_is_touched(
        self, db_session, mock_s3
    ):
        item = _make_item(db_session, "order")
        file_obj = _make_file(db_session, item)
        file_id = file_obj.id
        seen_in_database: list[bool] = []

        def delete_and_look(_key: str) -> None:
            db_session.expire_all()
            seen_in_database.append(db_session.get(ArchiveFile, file_id) is not None)

        with patch.object(mock_s3, "delete", side_effect=delete_and_look):
            hard_delete_file(db_session, mock_s3, file_obj)

        assert seen_in_database == [False]
