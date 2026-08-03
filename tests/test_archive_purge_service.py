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
    PurgeImpact,
    is_still_duplicate,
    list_deleted_files,
    list_deleted_files_in_dir,
    purge_file,
    refresh_candidate,
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
        assert c.filename == "testfile.jpg"
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
        assert list_deleted_files_in_dir(db_session, 999999) == []


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


class TestRefreshCandidate:
    def test_reflects_purge_of_sibling_within_same_session(self, db_session):
        item = _make_store_item(db_session)
        f1 = _make_file(db_session, item=item, deleted=True)
        f2 = _make_file(db_session, item=item, deleted=True)

        candidates = {c.file_id: c for c in list_deleted_files(db_session)}
        stale = candidates[f2.id]
        assert stale.other_deleted_sibling_count == 1
        assert stale.impact is PurgeImpact.SHARED

        # f1 gets purged elsewhere in the same walkthrough session.
        db_session.delete(f1)
        db_session.commit()

        fresh = refresh_candidate(db_session, stale)

        assert fresh.other_deleted_sibling_count == 0
        assert fresh.impact is PurgeImpact.SOLE
        assert fresh.file_id == stale.file_id
        assert fresh.filename == stale.filename

    def test_reflects_concurrent_soft_delete_of_active_sibling(self, db_session):
        item = _make_store_item(db_session)
        candidate_file = _make_file(db_session, item=item, deleted=True)
        active = _make_file(db_session, item=item, deleted=False)

        candidate = list_deleted_files(db_session)[0]
        assert candidate.file_id == candidate_file.id
        assert candidate.impact is PurgeImpact.DUPLICATE

        # The formerly active sibling gets soft-deleted too — its row still
        # exists (just now also soft-deleted), so this is a DUPLICATE ->
        # SHARED transition, not a jump straight to SOLE.
        active.deleted_at = _now()
        db_session.commit()

        fresh = refresh_candidate(db_session, candidate)

        assert fresh.active_sibling_count == 0
        assert fresh.other_deleted_sibling_count == 1
        assert fresh.impact is PurgeImpact.SHARED


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
