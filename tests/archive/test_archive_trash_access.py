"""A member may not read trashed content or foreign unfiled uploads."""

from datetime import UTC, datetime

import pytest

from tests.archive.test_archive_permissions import (
    _login_admin,
    _login_user,
    _make_dir,
    _make_file,
    _seed,
)


def _trash(db, obj):
    obj.deleted_at = datetime.now(UTC)
    db.commit()


@pytest.fixture
def member_headers(client, db_session):
    _seed(db_session)
    headers, _ = _login_user(db_session, client, org="vbw", state="fu")
    return headers


class TestTrashedFile:
    def test_detail_comment_and_url_are_404(self, client, db_session, member_headers):
        live = _make_dir(db_session, "Live", perms=["vbw_fu"])
        trashed = _make_file(db_session, live.id)
        _trash(db_session, trashed)
        fid = str(trashed.id)

        resp = client.get(f"/api/archive/files/{fid}", headers=member_headers)
        assert resp.status_code == 404
        resp = client.post(
            f"/api/archive/files/{fid}/comments",
            json={"content": "hallo welt"},
            headers=member_headers,
        )
        assert resp.status_code == 404

    def test_url_is_404(self, client, db_session, member_headers):
        live = _make_dir(db_session, "Live", perms=["vbw_fu"])
        trashed = _make_file(db_session, live.id)
        _trash(db_session, trashed)

        resp = client.get(
            f"/api/archive/files/{trashed.id}/url", headers=member_headers
        )

        assert resp.status_code == 404

    def test_admin_still_reads_trashed_file(self, client, db_session):
        _seed(db_session)
        admin_headers, _ = _login_admin(db_session, client)
        live = _make_dir(db_session, "Live", perms=["vbw_fu"])
        trashed = _make_file(db_session, live.id)
        _trash(db_session, trashed)

        resp = client.get(f"/api/archive/files/{trashed.id}", headers=admin_headers)

        assert resp.status_code == 200


class TestTrashedDirectory:
    def _tree(self, db_session):
        parent = _make_dir(db_session, "Papierkorb", perms=["vbw_fu"], recursive=True)
        child = _make_dir(db_session, "Unterordner", parent_id=parent.id)
        file_in_child = _make_file(db_session, child.id, desc="kindinhalt")
        _trash(db_session, parent)
        return str(parent.id), str(child.id), str(file_in_child.id)

    def test_directory_and_descendants_are_404(
        self, client, db_session, member_headers
    ):
        parent_id, child_id, file_id = self._tree(db_session)

        assert (
            client.get(
                f"/api/archive/dirs/{parent_id}", headers=member_headers
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/archive/dirs/{child_id}", headers=member_headers
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/archive/files/{file_id}", headers=member_headers
            ).status_code
            == 404
        )

    def test_search_hides_descendants(self, client, db_session, member_headers):
        self._tree(db_session)

        dirs = client.get(
            "/api/archive/search", params={"q": "Unterordner"}, headers=member_headers
        )
        files = client.get(
            "/api/archive/search", params={"q": "kindinhalt"}, headers=member_headers
        )

        assert dirs.json() == []
        assert files.json() == []

    def test_url_below_trashed_directory_is_404(
        self, client, db_session, member_headers
    ):
        _, _, file_id = self._tree(db_session)

        resp = client.get(f"/api/archive/files/{file_id}/url", headers=member_headers)

        assert resp.status_code == 404


class TestUnfiledUpload:
    def test_foreign_member_gets_no_url(self, client, db_session, member_headers):
        unfiled = _make_file(db_session, None)

        resp = client.get(
            f"/api/archive/files/{unfiled.id}/url", headers=member_headers
        )

        assert resp.status_code == 403

    def test_uploader_keeps_url_and_thumbnail(self, client, db_session):
        _seed(db_session)
        headers, member = _login_user(db_session, client, org="vbw", state="fu")
        unfiled = _make_file(db_session, None)
        unfiled.store_item.created_by = member.id
        db_session.commit()

        resp = client.get(f"/api/archive/files/{unfiled.id}/url", headers=headers)

        assert resp.status_code == 200
