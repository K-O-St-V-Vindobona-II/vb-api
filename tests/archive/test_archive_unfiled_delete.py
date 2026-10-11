"""Tests for a member permanently deleting their own unfiled upload."""

import hashlib
import logging
import os
import uuid
from unittest.mock import patch

from botocore.exceptions import ClientError
from sqlalchemy import event

from app.core.storage import S3_PATH_ARCHIVE_CACHE, S3_PATH_ARCHIVE_STORE
from app.models.archive_file import ArchiveFile
from app.models.archive_file_comment import ArchiveFileComment
from app.models.archive_store_item import ArchiveStoreItem
from tests.archive.helpers import (
    _login_user,
    _make_dir,
    _make_file,
    _now,
    _seed,
)

ALREADY_FILED_DETAIL = (
    "Die Datei wurde bereits einsortiert und kann nicht mehr gelöscht werden."
)


def _upload(client, headers, description="Test upload file") -> tuple[uuid.UUID, str]:
    """Uploads a file through the API; returns its id and content hash."""
    content = os.urandom(3 * 1024)
    resp = client.post(
        "/api/archive/upload",
        files={"file": ("test.jpg", content, "image/jpeg")},
        data={"description": description},
        headers=headers,
    )
    assert resp.status_code == 201
    return uuid.UUID(resp.json()["file"]["id"]), hashlib.sha256(content).hexdigest()


def _delete(client, headers, file_id):
    return client.delete(f"/api/archive/upload/unfiled/{file_id}", headers=headers)


class TestDeleteUnfiledUpload:
    def test_owner_removes_row_store_item_and_all_stored_objects(
        self, client, db_session, mock_s3
    ):
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        file_id, sha = _upload(client, headers)
        main_key = f"{S3_PATH_ARCHIVE_STORE}/{sha}"
        mock_s3.upload(f"{S3_PATH_ARCHIVE_CACHE}/{sha}.v2.thumb_sm", b"t", "image/jpeg")
        mock_s3.upload(f"{S3_PATH_ARCHIVE_CACHE}/{sha}.v2.thumb_lg", b"t", "image/jpeg")
        assert mock_s3.exists(main_key)

        resp = _delete(client, headers, file_id)

        assert resp.status_code == 204
        db_session.expire_all()
        assert db_session.get(ArchiveFile, file_id) is None
        assert (
            db_session.query(ArchiveStoreItem)
            .filter(ArchiveStoreItem.sha256_hash == sha)
            .count()
            == 0
        )
        assert not mock_s3.exists(main_key)
        assert mock_s3.list_keys(f"{S3_PATH_ARCHIVE_CACHE}/{sha}.") == []
        listing = client.get("/api/archive/upload/unfiled", headers=headers)
        assert listing.json()["files"] == []

    def test_other_member_gets_404_and_nothing_is_removed(
        self, client, db_session, mock_s3
    ):
        _seed(db_session)
        owner_headers, _ = _login_user(db_session, client, org="vbw", state="fu")
        other_headers, _ = _login_user(db_session, client, org="vbn", state="bi")
        file_id, sha = _upload(client, owner_headers)

        resp = _delete(client, other_headers, file_id)

        assert resp.status_code == 404
        assert db_session.get(ArchiveFile, file_id) is not None
        assert mock_s3.exists(f"{S3_PATH_ARCHIVE_STORE}/{sha}")

    def test_unknown_id_gets_404(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_user(db_session, client)

        resp = _delete(client, headers, uuid.uuid4())

        assert resp.status_code == 404

    def test_file_without_a_recorded_uploader_gets_404_for_everyone(
        self, client, db_session
    ):
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        legacy = _make_file(db_session, dir_id=None)

        resp = _delete(client, headers, legacy.id)

        assert resp.status_code == 404
        assert db_session.get(ArchiveFile, legacy.id) is not None

    def test_filed_upload_is_refused_with_409(self, client, db_session, mock_s3):
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        file_id, sha = _upload(client, headers)
        directory = _make_dir(db_session, "Filed")
        db_session.get(ArchiveFile, file_id).archive_dir_id = directory.id
        db_session.commit()

        resp = _delete(client, headers, file_id)

        assert resp.status_code == 409
        assert resp.json()["detail"] == ALREADY_FILED_DETAIL
        assert db_session.get(ArchiveFile, file_id) is not None
        assert mock_s3.exists(f"{S3_PATH_ARCHIVE_STORE}/{sha}")

    def test_unfiled_upload_trashed_by_an_admin_can_still_be_deleted(
        self, client, db_session
    ):
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        file_id, _ = _upload(client, headers)
        db_session.get(ArchiveFile, file_id).deleted_at = _now()
        db_session.commit()

        resp = _delete(client, headers, file_id)

        assert resp.status_code == 204
        db_session.expire_all()
        assert db_session.get(ArchiveFile, file_id) is None

    def test_content_still_used_by_another_file_is_kept(
        self, client, db_session, mock_s3
    ):
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        file_id, sha = _upload(client, headers)
        item_id = db_session.get(ArchiveFile, file_id).archive_store_item_id
        now = _now()
        sibling = ArchiveFile(
            archive_dir_id=_make_dir(db_session, "Elsewhere").id,
            description="sibling",
            archive_store_item_id=item_id,
            created_at=now,
            updated_at=now,
        )
        db_session.add(sibling)
        db_session.commit()

        resp = _delete(client, headers, file_id)

        assert resp.status_code == 204
        db_session.expire_all()
        assert db_session.get(ArchiveFile, file_id) is None
        assert db_session.get(ArchiveFile, sibling.id) is not None
        assert db_session.get(ArchiveStoreItem, item_id) is not None
        assert mock_s3.exists(f"{S3_PATH_ARCHIVE_STORE}/{sha}")

    def test_comments_are_removed_with_the_file(self, client, db_session):
        _seed(db_session)
        headers, member = _login_user(db_session, client)
        file_id, _ = _upload(client, headers)
        db_session.add(
            ArchiveFileComment(
                archive_file_id=file_id, content="A comment", created_by=member.id
            )
        )
        db_session.commit()

        resp = _delete(client, headers, file_id)

        assert resp.status_code == 204
        assert (
            db_session.query(ArchiveFileComment)
            .filter(ArchiveFileComment.archive_file_id == file_id)
            .count()
            == 0
        )

    def test_storage_failure_still_succeeds_and_is_logged(
        self, client, db_session, mock_s3, caplog
    ):
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        file_id, _ = _upload(client, headers)

        with (
            caplog.at_level(logging.WARNING, logger="app.services.archive_service"),
            patch.object(
                mock_s3._client,
                "delete_object",
                side_effect=ClientError(
                    {"Error": {"Code": "500", "Message": "fail"}}, "DeleteObject"
                ),
            ),
        ):
            resp = _delete(client, headers, file_id)

        assert resp.status_code == 204
        db_session.expire_all()
        assert db_session.get(ArchiveFile, file_id) is None
        assert "storage cleanup incomplete" in caplog.text
        assert str(file_id) in caplog.text

    def test_requires_login(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        file_id, _ = _upload(client, headers)

        resp = client.delete(f"/api/archive/upload/unfiled/{file_id}")

        assert resp.status_code == 401
        assert db_session.get(ArchiveFile, file_id) is not None

    def test_the_file_row_is_locked_while_it_is_checked(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        file_id, _ = _upload(client, headers)
        statements: list[str] = []

        def record(_conn, _cursor, statement, *_args):
            statements.append(statement)

        bind = db_session.get_bind()
        event.listen(bind, "before_cursor_execute", record)
        try:
            resp = _delete(client, headers, file_id)
        finally:
            event.remove(bind, "before_cursor_execute", record)

        assert resp.status_code == 204
        file_reads = [s for s in statements if "FROM archive_files" in s]
        assert file_reads
        assert "FOR UPDATE OF archive_files" in file_reads[0]

    def test_query_count_does_not_scale_with_the_number_of_uploads(
        self, client, db_session, count_queries
    ):
        _seed(db_session)
        headers, _ = _login_user(db_session, client)

        def delete_one_upload_and_count() -> int:
            file_id, _ = _upload(client, headers)
            with count_queries() as counter:
                assert _delete(client, headers, file_id).status_code == 204
            return counter.count

        for _ in range(2):
            _upload(client, headers)
        small = delete_one_upload_and_count()
        for _ in range(10):
            _upload(client, headers)
        large = delete_one_upload_and_count()

        assert large == small
