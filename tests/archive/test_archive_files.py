"""Tests für Archiv-Dateien (Upload, Download, Kommentare)."""

import os
from datetime import UTC, date, datetime

import bcrypt

from app.models.archive_dir import ArchiveDir
from app.models.archive_file import ArchiveFile
from app.models.archive_file_comment import (
    ArchiveFileComment,
)
from app.models.archive_file_version import (
    ArchiveFileVersion,
)
from app.models.archive_permission import (
    ArchivePermission,
)
from app.models.archive_store_item import (
    ArchiveStoreItem,
)
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import (
    create_user_session,
)


def _now():
    return datetime.now(UTC)


def _seed(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Org(id="vbn", label="VBN", order=2),
            State(id="fu", label="Fux", order=1),
            State(id="bi", label="Bandinhaber", order=3),
        ]
    )
    db.commit()
    db.add_all(
        [
            Role(
                id="internetreferent",
                group="funktion",
                label="Internetreferent",
                order=0,
            ),
            Role(
                id="x",
                group="chc",
                label="Senior",
                order=1,
            ),
        ]
    )
    db.commit()


def _login_admin(db, _client):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email="admin@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Admin",
        nachname="User",
        org_id="vbw",
        state_id="bi",
    )
    db.add(m)
    db.commit()
    db.add(
        MemberRole(
            member_id=m.id,
            role_id="internetreferent",
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    token, _, _ = create_user_session(db, m)
    return {"Authorization": f"Bearer {token}"}, m


def _login_user(db, _client, org="vbw", state="fu"):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email=f"{state}@{org}.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Normal",
        nachname="User",
        org_id=org,
        state_id=state,
    )
    db.add(m)
    db.commit()
    token, _, _ = create_user_session(db, m)
    return {"Authorization": f"Bearer {token}"}, m


def _make_dir(
    db,
    name,
    parent_id=0,
    perms=None,
    recursive=False,
):
    now = _now()
    d = ArchiveDir(
        name=name,
        archive_dir_id=parent_id,
        recursive_permissions=recursive,
        created_at=now,
        updated_at=now,
    )
    db.add(d)
    db.flush()
    for p in perms or []:
        parts = p.split("_")
        db.add(
            ArchivePermission(
                archive_dir_id=d.id,
                org_id=parts[0],
                state_id=parts[1],
            )
        )
    db.commit()
    return d


def _make_file(db, dir_id=0, desc="test"):
    now = _now()
    item = ArchiveStoreItem(
        name="testfile",
        original_name="testfile",
        extension="jpg",
        mime_type="image/jpeg",
        size=5000,
        sha256_hash=f"hash_{now.timestamp()}_{dir_id}",
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    db.flush()
    f = ArchiveFile(
        archive_dir_id=dir_id,
        description=desc,
    )
    db.add(f)
    db.flush()
    db.add(
        ArchiveFileVersion(
            archive_file_id=f.id,
            archive_store_item_id=item.id,
            active=True,
        )
    )
    db.commit()
    return f


def _valid_file_content(size_kb=3):
    """Create file content with enough bytes
    to pass the minimum size check."""
    return os.urandom(size_kb * 1024)


class TestUpload:
    def test_upload_success(
        self,
        client,
        db_session,
    ):
        """POST /archive/upload with valid file
        and description succeeds."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        content = _valid_file_content(3)
        resp = client.post(
            "/api/archive/upload",
            files={
                "file": ("test.jpg", content, "image/jpeg"),
            },
            data={"description": "Test upload file"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["file"]["extension"] == "jpg"

    def test_upload_duplicate_rejected(
        self,
        client,
        db_session,
    ):
        """Same SHA256 hash is rejected with 422."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        content = _valid_file_content(3)
        # First upload
        resp1 = client.post(
            "/api/archive/upload",
            files={
                "file": ("first.jpg", content, "image/jpeg"),
            },
            data={"description": "First upload here"},
            headers=headers,
        )
        assert resp1.status_code == 200
        # Second upload with identical content
        resp2 = client.post(
            "/api/archive/upload",
            files={
                "file": ("second.jpg", content, "image/jpeg"),
            },
            data={"description": "Duplicate upload"},
            headers=headers,
        )
        assert resp2.status_code == 422

    def test_upload_invalid_extension(
        self,
        client,
        db_session,
    ):
        """.exe file is rejected with 422."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        content = _valid_file_content(3)
        resp = client.post(
            "/api/archive/upload",
            files={
                "file": (
                    "malware.exe",
                    content,
                    "application/octet-stream",
                ),
            },
            data={"description": "Should be rejected"},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_upload_too_small(
        self,
        client,
        db_session,
    ):
        """File < 2KB is rejected."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        # 1KB = below the 2KB minimum
        content = os.urandom(1024)
        resp = client.post(
            "/api/archive/upload",
            files={
                "file": ("tiny.jpg", content, "image/jpeg"),
            },
            data={"description": "Tiny file upload"},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_upload_too_large(
        self,
        client,
        db_session,
    ):
        """File > 6MB is rejected."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        # 6145 KB = above the 6144 KB maximum
        content = os.urandom(6145 * 1024)
        resp = client.post(
            "/api/archive/upload",
            files={
                "file": ("huge.jpg", content, "image/jpeg"),
            },
            data={"description": "Huge file upload"},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_upload_description_too_short(
        self,
        client,
        db_session,
    ):
        """Description < 5 chars is rejected."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        content = _valid_file_content(3)
        resp = client.post(
            "/api/archive/upload",
            files={
                "file": ("test.jpg", content, "image/jpeg"),
            },
            data={"description": "Hi"},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_unfiled_uploads_shows_own(
        self,
        client,
        db_session,
    ):
        """GET /archive/upload/unfiled returns
        only current user's uploads."""
        _seed(db_session)
        headers_a, _user_a = _login_user(db_session, client, org="vbw", state="fu")
        headers_b, _user_b = _login_user(db_session, client, org="vbn", state="bi")
        # User A uploads a file
        content_a = _valid_file_content(3)
        resp_a = client.post(
            "/api/archive/upload",
            files={
                "file": ("a.jpg", content_a, "image/jpeg"),
            },
            data={"description": "File from user A"},
            headers=headers_a,
        )
        assert resp_a.status_code == 200
        # User B uploads a file
        content_b = _valid_file_content(4)
        resp_b = client.post(
            "/api/archive/upload",
            files={
                "file": ("b.jpg", content_b, "image/jpeg"),
            },
            data={"description": "File from user B"},
            headers=headers_b,
        )
        assert resp_b.status_code == 200
        # User A sees only their own unfiled upload
        resp = client.get(
            "/api/archive/upload/unfiled",
            headers=headers_a,
        )
        assert resp.status_code == 200
        files = resp.json()["files"]
        assert len(files) == 1
        assert files[0]["description"] == "File from user A"


class TestFileDetail:
    def test_file_detail(
        self,
        client,
        db_session,
    ):
        """GET /archive/files/{id} returns
        correct data."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "Dir")
        f = _make_file(db_session, dir_id=d.id, desc="photo")
        resp = client.get(
            f"/api/archive/files/{f.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["description"] == "photo"
        assert data["active_version"] is not None
        assert data["extension"] == "jpg"

    def test_file_update_description(
        self,
        client,
        db_session,
    ):
        """PUT /archive/files/{id} updates
        description."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        f = _make_file(db_session, desc="original")
        resp = client.put(
            f"/api/archive/files/{f.id}",
            json={"description": "updated desc"},
            headers=headers,
        )
        assert resp.status_code == 200
        db_session.refresh(f)
        assert f.description == "updated desc"


class TestComments:
    def test_comment_create(
        self,
        client,
        db_session,
    ):
        """POST /archive/files/{id}/comments
        creates a comment."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        d = _make_dir(db_session, "Dir", perms=["vbw_fu"])
        f = _make_file(db_session, dir_id=d.id)
        resp = client.post(
            f"/api/archive/files/{f.id}/comments",
            json={"content": "Tolle Datei!"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["comment"]["content"] == "Tolle Datei!"

    def test_comment_too_short_rejected(
        self,
        client,
        db_session,
    ):
        """Content < 5 chars is rejected with 422."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        d = _make_dir(db_session, "Dir", perms=["vbw_fu"])
        f = _make_file(db_session, dir_id=d.id)
        resp = client.post(
            f"/api/archive/files/{f.id}/comments",
            json={"content": "Hi"},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_comment_delete_requires_admin(
        self,
        client,
        db_session,
    ):
        """Non-admin cannot delete a comment (403)."""
        _seed(db_session)
        headers, user = _login_user(db_session, client)
        d = _make_dir(db_session, "Dir", perms=["vbw_fu"])
        f = _make_file(db_session, dir_id=d.id)
        c = ArchiveFileComment(
            archive_file_id=f.id,
            content="Test comment here",
            created_by=user.id,
            created_at=_now(),
        )
        db_session.add(c)
        db_session.commit()
        resp = client.delete(
            f"/api/archive/files/{f.id}/comments/{c.id}",
            headers=headers,
        )
        assert resp.status_code == 403


class TestDownload:
    def test_download_not_found(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.get(
            "/api/archive/files/99999/download",
            headers=headers,
        )
        assert resp.status_code == 404

    def test_download_requires_auth(
        self,
        client,
        db_session,
    ):
        resp = client.get("/api/archive/files/1/download")
        assert resp.status_code == 401

    def test_download_invalid_thumb_size(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "Dir")
        f = _make_file(db_session, dir_id=d.id)
        resp = client.get(
            f"/api/archive/files/{f.id}/download/xxl",
            headers=headers,
        )
        # Falls through to original download
        # (xxl is not in THUMB_SIZES)
        assert resp.status_code in (200, 404)


class TestPresignedUrl:
    def test_file_url_returns_url(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "Dir")
        f = _make_file(db_session, dir_id=d.id)
        resp = client.get(
            f"/api/archive/files/{f.id}/url",
            headers=headers,
        )
        assert resp.status_code == 200
        assert "url" in resp.json()
        assert resp.json()["url"].startswith("http")

    def test_file_url_with_size(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "Dir")
        f = _make_file(db_session, dir_id=d.id)
        resp = client.get(
            f"/api/archive/files/{f.id}/url/md",
            headers=headers,
        )
        assert resp.status_code == 200
        assert "url" in resp.json()

    def test_file_url_not_found(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.get(
            "/api/archive/files/99999/url",
            headers=headers,
        )
        assert resp.status_code == 404

    def test_file_url_requires_auth(
        self,
        client,
        db_session,
    ):
        resp = client.get("/api/archive/files/1/url")
        assert resp.status_code == 401


class TestMoveAndRestore:
    def test_move_file_to_dir(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        target = _make_dir(db_session, "Target")
        f = _make_file(db_session, dir_id=0)
        resp = client.post(
            f"/api/archive/dirs/{target.id}/receive",
            json={
                "type": "file",
                "ids": [f.id],
                "action": "move",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        db_session.expire_all()
        updated = db_session.get(ArchiveFile, f.id)
        assert updated.archive_dir_id == target.id

    def test_move_file_to_root(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "Dir")
        f = _make_file(db_session, dir_id=d.id)
        resp = client.post(
            "/api/archive/dirs/receive",
            json={
                "type": "file",
                "ids": [f.id],
                "action": "move",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        db_session.expire_all()
        updated = db_session.get(ArchiveFile, f.id)
        assert updated.archive_dir_id == 0

    def test_restore_deleted_file(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        f = _make_file(db_session)
        client.delete(
            f"/api/archive/files/{f.id}",
            headers=headers,
        )
        db_session.expire_all()
        assert db_session.get(ArchiveFile, f.id).deleted_at is not None

        resp = client.patch(
            f"/api/archive/files/{f.id}/restore",
            headers=headers,
        )
        assert resp.status_code == 200
        db_session.expire_all()
        assert db_session.get(ArchiveFile, f.id).deleted_at is None

    def test_dir_detail_content_categories(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _admin = _login_admin(db_session, client)
        d = _make_dir(
            db_session,
            "Parent",
            perms=["vbw_bi"],
            recursive=True,
        )
        f_active = _make_file(db_session, dir_id=d.id, desc="active")
        f_deleted = _make_file(db_session, dir_id=d.id, desc="deleted")
        f_deleted.deleted_at = _now()
        db_session.commit()

        resp = client.get(
            f"/api/archive/dirs/{d.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        content = resp.json()["content"]
        insight_ids = [f["id"] for f in content["files"]["insight"]]
        trashed_ids = [f["id"] for f in content["files"]["trashed"]]
        assert f_active.id in insight_ids
        assert f_deleted.id in trashed_ids

    def test_move_requires_admin(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        d = _make_dir(db_session, "Dir", perms=["vbw_fu"])
        f = _make_file(db_session, dir_id=d.id)
        resp = client.post(
            f"/api/archive/dirs/{d.id}/receive",
            json={
                "type": "file",
                "ids": [f.id],
                "action": "move",
            },
            headers=headers,
        )
        assert resp.status_code == 403
