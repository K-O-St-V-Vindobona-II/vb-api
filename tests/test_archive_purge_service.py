"""Tests for archive_purge_service.py — hard-delete of soft-deleted archive
files (DB + S3), including the store-item reference-counting logic that
decides whether the underlying S3 object may safely be removed.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from app.core.storage import S3_PATH_ARCHIVE_CACHE, S3_PATH_ARCHIVE_STORE
from app.models.archive_dir import ArchiveDir
from app.models.archive_file import ArchiveFile
from app.models.archive_file_comment import ArchiveFileComment
from app.models.archive_store_item import ArchiveStoreItem
from app.models.member import Member
from app.services.archive_purge_service import (
    PurgeError,
    list_deleted_files,
    purge_file,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _make_member(db, vorname="Max", nachname="Muster"):
    m = Member(vorname=vorname, nachname=nachname)
    db.add(m)
    db.flush()
    return m


def _make_dir(db, name="Fotos", parent_id=0):
    now = _now()
    d = ArchiveDir(name=name, archive_dir_id=parent_id, created_at=now, updated_at=now)
    db.add(d)
    db.flush()
    return d


def _make_store_item(db, hash_suffix="", size=5000, created_by=None):
    now = _now()
    item = ArchiveStoreItem(
        name="testfile",
        extension="jpg",
        mime_type="image/jpeg",
        size=size,
        sha256_hash=f"hash_{now.timestamp()}_{hash_suffix}",
        created_by=created_by.id if created_by else None,
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    db.flush()
    return item


def _make_file(
    db,
    *,
    dir_id=0,
    desc="test",
    item=None,
    deleted=True,
):
    now = _now()
    if item is None:
        item = _make_store_item(db)
    f = ArchiveFile(
        archive_dir_id=dir_id,
        description=desc,
        archive_store_item_id=item.id,
        created_at=now,
        updated_at=now,
        deleted_at=now if deleted else None,
    )
    db.add(f)
    db.commit()
    return f


class TestListDeletedFiles:
    def test_empty(self, db_session):
        assert list_deleted_files(db_session) == []

    def test_returns_candidates_with_expected_fields(self, db_session):
        member = _make_member(db_session, "Erika", "Musterfrau")
        item = _make_store_item(db_session, created_by=member)
        d = _make_dir(db_session, "Sommerfest")
        f = _make_file(db_session, dir_id=d.id, desc="Gruppenfoto", item=item)

        candidates = list_deleted_files(db_session)

        assert len(candidates) == 1
        c = candidates[0]
        assert c.file_id == f.id
        assert c.path == "Sommerfest"
        assert c.description == "Gruppenfoto"
        assert c.deleted_at is not None
        assert c.size == item.size
        assert c.sha256_hash == item.sha256_hash
        assert c.created_by == "Erika Musterfrau"

    def test_excludes_active_files(self, db_session):
        _make_file(db_session, desc="active", deleted=False)
        deleted = _make_file(db_session, desc="deleted", deleted=True)

        candidates = list_deleted_files(db_session)

        assert [c.file_id for c in candidates] == [deleted.id]


class TestPurgeFileValidation:
    def test_not_found_raises(self, db_session, mock_s3):
        with pytest.raises(PurgeError):
            purge_file(db_session, mock_s3, 999999)

    def test_not_soft_deleted_raises(self, db_session, mock_s3):
        f = _make_file(db_session, deleted=False)

        with pytest.raises(PurgeError):
            purge_file(db_session, mock_s3, f.id)

        assert db_session.get(ArchiveFile, f.id) is not None


class TestPurgeFileCascade:
    def test_deletes_comment_rows(self, db_session, mock_s3):
        f = _make_file(db_session)
        db_session.add(ArchiveFileComment(archive_file_id=f.id, content="hi"))
        db_session.commit()

        purge_file(db_session, mock_s3, f.id)

        assert db_session.get(ArchiveFile, f.id) is None
        assert (
            db_session.query(ArchiveFileComment)
            .filter(ArchiveFileComment.archive_file_id == f.id)
            .count()
            == 0
        )


class TestPurgeFileS3Cleanup:
    def test_deletes_orphaned_store_item_and_s3_objects(self, db_session, mock_s3):
        item = _make_store_item(db_session)
        f = _make_file(db_session, item=item)
        main_key = f"{S3_PATH_ARCHIVE_STORE}/{item.sha256_hash}"
        cache_key = f"{S3_PATH_ARCHIVE_CACHE}/{item.sha256_hash}.v1.thumb_sm"
        mock_s3.upload(main_key, b"content", "image/jpeg")
        mock_s3.upload(cache_key, b"thumb", "image/jpeg")

        result = purge_file(db_session, mock_s3, f.id)

        assert db_session.get(ArchiveStoreItem, item.id) is None
        assert mock_s3.exists(main_key) is False
        assert mock_s3.list_keys(f"{S3_PATH_ARCHIVE_CACHE}/{item.sha256_hash}.") == []
        assert result.store_item_deleted is True
        assert set(result.s3_keys_deleted) == {main_key, cache_key}
        assert result.s3_errors == []

    def test_removes_all_thumbnail_cache_variants(self, db_session, mock_s3):
        item = _make_store_item(db_session)
        f = _make_file(db_session, item=item)
        cache_prefix = f"{S3_PATH_ARCHIVE_CACHE}/{item.sha256_hash}."
        for variant in ("v1.thumb_sm", "v1.thumb_md", "v2.thumb_sm"):
            mock_s3.upload(f"{cache_prefix}{variant}", b"thumb", "image/jpeg")

        purge_file(db_session, mock_s3, f.id)

        assert mock_s3.list_keys(cache_prefix) == []

    def test_keeps_store_item_still_referenced_by_another_file(
        self, db_session, mock_s3
    ):
        item = _make_store_item(db_session)
        main_key = f"{S3_PATH_ARCHIVE_STORE}/{item.sha256_hash}"
        mock_s3.upload(main_key, b"content", "image/jpeg")
        f1 = _make_file(db_session, item=item)
        _make_file(db_session, item=item, deleted=False)

        result = purge_file(db_session, mock_s3, f1.id)

        assert db_session.get(ArchiveStoreItem, item.id) is not None
        assert mock_s3.exists(main_key) is True
        assert result.store_item_deleted is False
        assert result.s3_keys_deleted == []


class TestPurgeFileOrdering:
    def test_db_committed_before_s3_error(self, db_session, mock_s3):
        item = _make_store_item(db_session)
        f = _make_file(db_session, item=item)
        mock_s3.upload(
            f"{S3_PATH_ARCHIVE_STORE}/{item.sha256_hash}", b"content", "image/jpeg"
        )

        with patch.object(
            mock_s3._client,
            "delete_object",
            side_effect=ClientError(
                {"Error": {"Code": "500", "Message": "fail"}}, "DeleteObject"
            ),
        ):
            result = purge_file(db_session, mock_s3, f.id)

        assert db_session.get(ArchiveFile, f.id) is None
        assert db_session.get(ArchiveStoreItem, item.id) is None
        assert result.s3_errors != []

    def test_records_s3_errors_without_aborting(self, db_session, mock_s3):
        item = _make_store_item(db_session)
        f = _make_file(db_session, item=item)
        main_key = f"{S3_PATH_ARCHIVE_STORE}/{item.sha256_hash}"
        cache_key = f"{S3_PATH_ARCHIVE_CACHE}/{item.sha256_hash}.v1.thumb_sm"
        mock_s3.upload(main_key, b"content", "image/jpeg")
        mock_s3.upload(cache_key, b"thumb", "image/jpeg")

        original_delete_object = mock_s3._client.delete_object

        def failing_delete_object(**kwargs):
            if kwargs.get("Key") == main_key:
                raise ClientError(
                    {"Error": {"Code": "500", "Message": "fail"}}, "DeleteObject"
                )
            return original_delete_object(**kwargs)

        with patch.object(
            mock_s3._client, "delete_object", side_effect=failing_delete_object
        ):
            result = purge_file(db_session, mock_s3, f.id)

        assert result.s3_keys_deleted == [cache_key]
        assert len(result.s3_errors) == 1
        assert main_key in result.s3_errors[0]
