"""Tests for archive_maintenance_service.py — hard-delete/restore of
soft-deleted archive files (DB + S3) and active-duplicate inspection,
including the store-item reference-counting logic that decides whether the
underlying S3 object may safely be removed.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from app.core.storage import S3_PATH_ARCHIVE_CACHE, S3_PATH_ARCHIVE_STORE
from app.models.archive_dir import ArchiveDir
from app.models.archive_file import ArchiveFile
from app.models.archive_file_comment import ArchiveFileComment
from app.models.archive_store_item import ArchiveStoreItem
from app.services.archive_maintenance_service import (
    ArchiveMaintenanceError,
    PurgeImpact,
    active_duplicates_in_dir,
    active_duplicates_of_file,
    find_dir_location,
    find_file_location,
    is_still_duplicate,
    list_deleted_files,
    list_deleted_files_in_dir,
    purge_file,
    restore_file,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _make_dir(db, name="Fotos", parent_id=None):
    now = _now()
    d = ArchiveDir(name=name, archive_dir_id=parent_id, created_at=now, updated_at=now)
    db.add(d)
    db.flush()
    return d


def _make_store_item(db, hash_suffix="", size=5000):
    now = _now()
    item = ArchiveStoreItem(
        name="testfile",
        extension="jpg",
        mime_type="image/jpeg",
        size=size,
        sha256_hash=f"hash_{now.timestamp()}_{hash_suffix}",
        created_by=None,
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    db.flush()
    return item


def _make_file(
    db,
    *,
    dir_id=None,
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
        item = _make_store_item(db_session)
        d = _make_dir(db_session, "Sommerfest")
        f = _make_file(db_session, dir_id=d.id, desc="Gruppenfoto", item=item)

        candidates = list_deleted_files(db_session)

        assert len(candidates) == 1
        c = candidates[0]
        assert c.file_id == f.id
        assert c.path == "Sommerfest"
        assert c.archive_dir_id == d.id
        assert c.name == "testfile"
        assert c.extension == "jpg"
        assert c.filename == "testfile.jpg"
        assert c.description == "Gruppenfoto"
        assert c.deleted_at is not None
        assert c.size == item.size
        assert c.sha256_hash == item.sha256_hash

    def test_excludes_active_files(self, db_session):
        _make_file(db_session, desc="active", deleted=False)
        deleted = _make_file(db_session, desc="deleted", deleted=True)

        candidates = list_deleted_files(db_session)

        assert [c.file_id for c in candidates] == [deleted.id]


class TestPurgeCandidateImpact:
    def test_duplicate_when_active_sibling_exists(self, db_session):
        item = _make_store_item(db_session)
        deleted = _make_file(db_session, item=item, deleted=True)
        _make_file(db_session, item=item, deleted=False)

        candidates = list_deleted_files(db_session)

        assert len(candidates) == 1
        c = candidates[0]
        assert c.file_id == deleted.id
        assert c.active_sibling_count == 1
        assert c.other_deleted_sibling_count == 0
        assert c.impact is PurgeImpact.DUPLICATE

    def test_shared_when_only_other_deleted_siblings_exist(self, db_session):
        item = _make_store_item(db_session)
        f1 = _make_file(db_session, item=item, deleted=True)
        f2 = _make_file(db_session, item=item, deleted=True)

        candidates = {c.file_id: c for c in list_deleted_files(db_session)}

        assert candidates[f1.id].active_sibling_count == 0
        assert candidates[f1.id].other_deleted_sibling_count == 1
        assert candidates[f1.id].impact is PurgeImpact.SHARED
        assert candidates[f2.id].other_deleted_sibling_count == 1
        assert candidates[f2.id].impact is PurgeImpact.SHARED

    def test_sole_when_no_other_reference(self, db_session):
        f = _make_file(db_session, deleted=True)

        candidates = list_deleted_files(db_session)

        assert len(candidates) == 1
        assert candidates[0].file_id == f.id
        assert candidates[0].active_sibling_count == 0
        assert candidates[0].other_deleted_sibling_count == 0
        assert candidates[0].impact is PurgeImpact.SOLE

    def test_other_deleted_sibling_count_excludes_self(self, db_session):
        item = _make_store_item(db_session)
        files = [_make_file(db_session, item=item, deleted=True) for _ in range(3)]

        candidates = {c.file_id: c for c in list_deleted_files(db_session)}

        for f in files:
            assert candidates[f.id].other_deleted_sibling_count == 2
            assert candidates[f.id].impact is PurgeImpact.SHARED


class TestListDeletedFilesQueryCount:
    def test_reference_counting_does_not_n_plus_one(self, db_session, count_queries):
        """Regression test: computing active/deleted sibling counts for every
        candidate must stay a single aggregated query, not one per file."""
        _make_file(db_session, deleted=True)

        with count_queries() as small:
            small_result = list_deleted_files(db_session)

        for _ in range(10):
            _make_file(db_session, deleted=True)

        with count_queries() as large:
            large_result = list_deleted_files(db_session)

        assert len(small_result) == 1
        assert len(large_result) == 11
        assert large.count == small.count


class TestListDeletedFilesInDir:
    def test_scopes_to_direct_children_only(self, db_session):
        dir_a = _make_dir(db_session, "DirA")
        dir_b = _make_dir(db_session, "DirB")
        in_a = _make_file(db_session, dir_id=dir_a.id, deleted=True)
        _make_file(db_session, dir_id=dir_b.id, deleted=True)

        candidates = list_deleted_files_in_dir(db_session, dir_a.id)

        assert [c.file_id for c in candidates] == [in_a.id]

    def test_excludes_subdirectories(self, db_session):
        parent = _make_dir(db_session, "Parent")
        child = _make_dir(db_session, "Child", parent_id=parent.id)
        in_child = _make_file(db_session, dir_id=child.id, deleted=True)

        candidates = list_deleted_files_in_dir(db_session, parent.id)

        assert candidates == []
        assert [c.file_id for c in list_deleted_files_in_dir(db_session, child.id)] == [
            in_child.id
        ]

    def test_excludes_active_files(self, db_session):
        d = _make_dir(db_session, "Fotos")
        _make_file(db_session, dir_id=d.id, deleted=False)
        deleted = _make_file(db_session, dir_id=d.id, deleted=True)

        candidates = list_deleted_files_in_dir(db_session, d.id)

        assert [c.file_id for c in candidates] == [deleted.id]

    def test_empty_dir_returns_empty_list(self, db_session):
        assert list_deleted_files_in_dir(db_session, uuid.uuid4()) == []


class TestIsStillDuplicate:
    def test_true_when_active_sibling_exists(self, db_session):
        item = _make_store_item(db_session)
        deleted = _make_file(db_session, item=item, deleted=True)
        _make_file(db_session, item=item, deleted=False)

        candidate = list_deleted_files(db_session)[0]
        assert candidate.file_id == deleted.id
        assert is_still_duplicate(db_session, candidate) is True

    def test_false_when_no_active_sibling(self, db_session):
        _make_file(db_session, deleted=True)

        candidate = list_deleted_files(db_session)[0]
        assert is_still_duplicate(db_session, candidate) is False

    def test_reflects_concurrent_soft_delete_of_active_sibling(self, db_session):
        item = _make_store_item(db_session)
        deleted = _make_file(db_session, item=item, deleted=True)
        active = _make_file(db_session, item=item, deleted=False)

        candidate = list_deleted_files(db_session)[0]
        assert candidate.file_id == deleted.id
        assert is_still_duplicate(db_session, candidate) is True

        # Simulate a concurrent soft-delete of the active sibling happening
        # between listing and the batch purge actually running.
        active.deleted_at = _now()
        db_session.commit()

        assert is_still_duplicate(db_session, candidate) is False


class TestPurgeFileValidation:
    def test_not_found_raises(self, db_session, mock_s3):
        with pytest.raises(ArchiveMaintenanceError):
            purge_file(db_session, mock_s3, uuid.uuid4())

    def test_not_soft_deleted_raises(self, db_session, mock_s3):
        f = _make_file(db_session, deleted=False)

        with pytest.raises(ArchiveMaintenanceError):
            purge_file(db_session, mock_s3, f.id)

        assert db_session.get(ArchiveFile, f.id) is not None


class TestPurgeFileCascade:
    def test_deletes_comment_rows(self, db_session, mock_s3):
        f = _make_file(db_session)
        file_id = f.id
        db_session.add(ArchiveFileComment(archive_file_id=file_id, content="hi"))
        db_session.commit()

        purge_file(db_session, mock_s3, file_id)

        assert db_session.get(ArchiveFile, file_id) is None
        assert (
            db_session.query(ArchiveFileComment)
            .filter(ArchiveFileComment.archive_file_id == file_id)
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


class TestRestoreFile:
    def test_restores_and_returns_location(self, db_session):
        d = _make_dir(db_session, "Sommerfest")
        f = _make_file(db_session, dir_id=d.id, deleted=True)

        result = restore_file(db_session, f.id)

        assert db_session.get(ArchiveFile, f.id).deleted_at is None
        assert result.file_id == f.id
        assert result.path == "Sommerfest"
        assert result.name == "testfile"
        assert result.extension == "jpg"
        assert result.filename == "testfile.jpg"
        assert result.deleted is False

    def test_not_found_raises(self, db_session):
        with pytest.raises(ArchiveMaintenanceError):
            restore_file(db_session, uuid.uuid4())

    def test_not_currently_deleted_raises(self, db_session):
        f = _make_file(db_session, deleted=False)

        with pytest.raises(ArchiveMaintenanceError):
            restore_file(db_session, f.id)

        assert db_session.get(ArchiveFile, f.id).deleted_at is None

    def test_duplicate_impact_raises_and_stays_deleted(self, db_session):
        item = _make_store_item(db_session)
        deleted = _make_file(db_session, item=item, deleted=True)
        _make_file(db_session, item=item, deleted=False)

        with pytest.raises(ArchiveMaintenanceError):
            restore_file(db_session, deleted.id)

        assert db_session.get(ArchiveFile, deleted.id).deleted_at is not None

    def test_shared_impact_raises_and_stays_deleted(self, db_session):
        item = _make_store_item(db_session)
        f1 = _make_file(db_session, item=item, deleted=True)
        _make_file(db_session, item=item, deleted=True)

        with pytest.raises(ArchiveMaintenanceError):
            restore_file(db_session, f1.id)

        assert db_session.get(ArchiveFile, f1.id).deleted_at is not None


class TestActiveDuplicatesOfFile:
    def test_no_duplicates(self, db_session):
        f = _make_file(db_session, deleted=True)

        location, duplicates = active_duplicates_of_file(db_session, f.id)

        assert location.file_id == f.id
        assert location.deleted is True
        assert duplicates == []

    def test_finds_active_duplicates_with_path(self, db_session):
        item = _make_store_item(db_session)
        d = _make_dir(db_session, "Archiv2")
        target = _make_file(db_session, item=item, deleted=True)
        active = _make_file(db_session, dir_id=d.id, item=item, deleted=False)

        _, duplicates = active_duplicates_of_file(db_session, target.id)

        assert [dup.file_id for dup in duplicates] == [active.id]
        assert duplicates[0].path == "Archiv2"
        assert duplicates[0].filename == "testfile.jpg"

    def test_excludes_deleted_siblings(self, db_session):
        item = _make_store_item(db_session)
        target = _make_file(db_session, item=item, deleted=True)
        _make_file(db_session, item=item, deleted=True)

        _, duplicates = active_duplicates_of_file(db_session, target.id)

        assert duplicates == []

    def test_excludes_itself_even_if_active(self, db_session):
        f = _make_file(db_session, deleted=False)

        location, duplicates = active_duplicates_of_file(db_session, f.id)

        assert location.deleted is False
        assert duplicates == []

    def test_not_found_raises(self, db_session):
        with pytest.raises(ArchiveMaintenanceError):
            active_duplicates_of_file(db_session, uuid.uuid4())


class TestActiveDuplicatesInDir:
    def test_empty_dir_returns_empty_list(self, db_session):
        assert active_duplicates_in_dir(db_session, uuid.uuid4()) == []

    def test_pairs_each_deleted_file_with_its_duplicates(self, db_session):
        d = _make_dir(db_session, "Fotos")
        item = _make_store_item(db_session)
        with_dup = _make_file(db_session, dir_id=d.id, item=item, deleted=True)
        _make_file(db_session, item=item, deleted=False)
        without_dup = _make_file(db_session, dir_id=d.id, deleted=True)

        pairs = active_duplicates_in_dir(db_session, d.id)
        duplicates_by_id = {c.file_id: dups for c, dups in pairs}

        assert len(duplicates_by_id[with_dup.id]) == 1
        assert duplicates_by_id[without_dup.id] == []

    def test_no_n_plus_one(self, db_session, count_queries):
        d = _make_dir(db_session, "Fotos")
        _make_file(db_session, dir_id=d.id, deleted=True)

        with count_queries() as small:
            small_result = active_duplicates_in_dir(db_session, d.id)

        for _ in range(10):
            _make_file(db_session, dir_id=d.id, deleted=True)

        with count_queries() as large:
            large_result = active_duplicates_in_dir(db_session, d.id)

        assert len(small_result) == 1
        assert len(large_result) == 11
        assert large.count == small.count


class TestFindFileLocation:
    def test_finds_deleted_file(self, db_session):
        d = _make_dir(db_session, "Sommerfest")
        f = _make_file(db_session, dir_id=d.id, deleted=True)

        location = find_file_location(db_session, f.id)

        assert location is not None
        assert location.file_id == f.id
        assert location.path == "Sommerfest"
        assert location.filename == "testfile.jpg"
        assert location.deleted is True

    def test_finds_active_file(self, db_session):
        f = _make_file(db_session, deleted=False)

        location = find_file_location(db_session, f.id)

        assert location is not None
        assert location.deleted is False

    def test_returns_none_when_not_found(self, db_session):
        assert find_file_location(db_session, uuid.uuid4()) is None


class TestFindDirLocation:
    def test_finds_active_dir(self, db_session):
        d = _make_dir(db_session, "Sommerfest")

        location = find_dir_location(db_session, d.id)

        assert location is not None
        assert location.dir_id == d.id
        assert location.path == "Sommerfest"
        assert location.deleted is False

    def test_finds_deleted_dir(self, db_session):
        d = _make_dir(db_session, "Sommerfest")
        d.deleted_at = _now()
        db_session.commit()

        location = find_dir_location(db_session, d.id)

        assert location is not None
        assert location.deleted is True

    def test_returns_none_when_not_found(self, db_session):
        assert find_dir_location(db_session, uuid.uuid4()) is None
