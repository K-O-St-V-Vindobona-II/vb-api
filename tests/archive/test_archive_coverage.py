"""Tests to increase coverage for app/services/archive_service.py.

Targets uncovered lines: 160-162, 256-258, 270-275, 296, 330-331, 344,
361, 408-410, 440-456, 467, 494, 508, 521, 523, 546, 580, 631, 690,
707, 723, 750, 762-765, 778-780, 791-793, 823, 852, 858-867, 912,
1016, 1042, 1059-1060, 1068-1148.
"""

import os
from datetime import UTC, date, datetime
from io import BytesIO

import bcrypt
import pytest
from PIL import Image as PILImage

from app.core.storage import THUMBNAIL_CACHE_VERSION
from app.models.archive_dir import ArchiveDir
from app.models.archive_file import ArchiveFile
from app.models.archive_file_comment import ArchiveFileComment
from app.models.archive_file_version import ArchiveFileVersion
from app.models.archive_permission import ArchivePermission
from app.models.archive_store_item import ArchiveStoreItem
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.schemas.archive import (
    CommentCreateRequest,
    DirReceiveRequest,
    DirSaveRequest,
    FileUpdateRequest,
)
from app.services.archive_service import (
    _active_store_item,
    _file_short,
    _get_or_create_thumbnail,
    _is_descendant,
    _serve_thumbnail,
    get_unsorted_upload_count,
    receive_items,
)
from app.services.auth_service import create_user_session


def _now() -> datetime:
    return datetime.now(UTC)


def _seed(db) -> None:
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


def _login_admin(db, _client) -> tuple[dict[str, str], Member]:
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


def _login_user(
    db,
    _client,
    org: str = "vbw",
    state: str = "fu",
) -> tuple[dict[str, str], Member]:
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
    name: str,
    parent_id: int = 0,
    perms: list[str] | None = None,
    recursive: bool = False,
) -> ArchiveDir:
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


def _make_file(
    db,
    dir_id: int = 0,
    desc: str = "test",
    extension: str = "jpg",
    mime_type: str = "image/jpeg",
    hash_suffix: str = "",
) -> ArchiveFile:
    now = _now()
    item = ArchiveStoreItem(
        name="testfile",
        original_name="testfile",
        extension=extension,
        mime_type=mime_type,
        size=5000,
        sha256_hash=f"hash_{now.timestamp()}_{dir_id}_{hash_suffix}",
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


def _make_file_no_active_version(
    db,
    dir_id: int = 0,
    desc: str = "no-active",
) -> ArchiveFile:
    """Create a file with a version where active=False (legacy fallback)."""
    now = _now()
    item = ArchiveStoreItem(
        name="legacyfile",
        original_name="legacyfile",
        extension="jpg",
        mime_type="image/jpeg",
        size=3000,
        sha256_hash=f"legacy_{now.timestamp()}_{dir_id}",
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
            active=False,
        )
    )
    db.commit()
    return f


def _make_file_no_version(
    db,
    dir_id: int = 0,
    desc: str = "no-version",
) -> ArchiveFile:
    """Create a file with no file versions at all."""
    f = ArchiveFile(
        archive_dir_id=dir_id,
        description=desc,
    )
    db.add(f)
    db.commit()
    return f


def _make_jpeg_bytes(width: int = 100, height: int = 100) -> bytes:
    """Generate a minimal valid JPEG image in memory."""
    img = PILImage.new("RGB", (width, height), color=(255, 0, 0))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _valid_file_content(size_kb: int = 3) -> bytes:
    return os.urandom(size_kb * 1024)


# ------------------------------------------------------------------ #
# _active_store_item fallback and empty paths (lines 160-162)
# ------------------------------------------------------------------ #


class TestActiveStoreItemFallback:
    def test_fallback_to_first_version_when_no_active_flag(
        self,
        db_session,
    ):
        """When no version has active=True, fall back to the first version."""
        _seed(db_session)
        f = _make_file_no_active_version(db_session)
        item = _active_store_item(f)
        assert item is not None
        assert item.name == "legacyfile"

    def test_returns_none_when_no_versions_exist(
        self,
        db_session,
    ):
        """File with zero file_versions returns None."""
        _seed(db_session)
        f = _make_file_no_version(db_session)
        item = _active_store_item(f)
        assert item is None

    def test_file_short_with_no_store_item(
        self,
        db_session,
    ):
        """_file_short handles a file with no store item gracefully."""
        _seed(db_session)
        f = _make_file_no_version(db_session)
        result = _file_short(f)
        assert result["name"] is None
        assert result["extension"] is None
        assert result["size"] == 0
        assert result["is_image"] is False
        assert result["mime_type"] is None


# ------------------------------------------------------------------ #
# Classification: trashed dirs/files at root (lines 256-258, 270-275, 296)
# ------------------------------------------------------------------ #


class TestRootContentClassification:
    def test_admin_sees_trashed_dir_at_root(
        self,
        client,
        db_session,
    ):
        """Admin sees soft-deleted root dirs in the trashed bucket."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "TrashedDir")
        d.deleted_at = _now()
        db_session.commit()
        resp = client.get("/api/archive/dirs", headers=headers)
        assert resp.status_code == 200
        trashed = resp.json()["content"]["subdirs"]["trashed"]
        trashed_ids = [x["id"] for x in trashed]
        assert d.id in trashed_ids

    def test_normal_user_does_not_see_trashed_dir_at_root(
        self,
        client,
        db_session,
    ):
        """Non-admin does not see trashed root dirs."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        d = _make_dir(db_session, "TrashedDir2", perms=["vbw_fu"])
        d.deleted_at = _now()
        db_session.commit()
        resp = client.get("/api/archive/dirs", headers=headers)
        assert resp.status_code == 200
        trashed = resp.json()["content"]["subdirs"]["trashed"]
        assert len(trashed) == 0

    def test_admin_sees_root_files_in_admin_bucket(
        self,
        client,
        db_session,
    ):
        """Root-level files appear in admin bucket for admin users."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        f = _make_file(db_session, dir_id=0, desc="root-file")
        resp = client.get("/api/archive/dirs", headers=headers)
        assert resp.status_code == 200
        admin_files = resp.json()["content"]["files"]["admin"]
        admin_ids = [x["id"] for x in admin_files]
        assert f.id in admin_ids

    def test_admin_sees_trashed_root_file(
        self,
        client,
        db_session,
    ):
        """Admin sees soft-deleted root files in trashed bucket."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        f = _make_file(db_session, dir_id=0, desc="trashed-root")
        f.deleted_at = _now()
        db_session.commit()
        resp = client.get("/api/archive/dirs", headers=headers)
        assert resp.status_code == 200
        trashed = resp.json()["content"]["files"]["trashed"]
        trashed_ids = [x["id"] for x in trashed]
        assert f.id in trashed_ids

    def test_normal_user_does_not_see_root_files(
        self,
        client,
        db_session,
    ):
        """Non-admin does not see root-level files."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        _make_file(db_session, dir_id=0, desc="hidden-root")
        resp = client.get("/api/archive/dirs", headers=headers)
        assert resp.status_code == 200
        all_files = resp.json()["content"]["files"]
        total = (
            len(all_files["insight"])
            + len(all_files["admin"])
            + len(all_files["trashed"])
        )
        assert total == 0


# ------------------------------------------------------------------ #
# _classify_file_in_dir admin-only branch (lines 330-331, 344)
# ------------------------------------------------------------------ #


class TestDirDetailClassification:
    def test_admin_sees_file_in_admin_bucket_without_insight(
        self,
        client,
        db_session,
    ):
        """Admin sees files in admin bucket when they lack insight permission."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        # Dir with permission vbn_fu: admin (vbw_bi) has no insight
        d = _make_dir(db_session, "NoInsight", perms=["vbn_fu"])
        f = _make_file(db_session, dir_id=d.id, desc="admin-only-file")
        resp = client.get(f"/api/archive/dirs/{d.id}", headers=headers)
        assert resp.status_code == 200
        content = resp.json()["content"]
        admin_ids = [x["id"] for x in content["files"]["admin"]]
        insight_ids = [x["id"] for x in content["files"]["insight"]]
        assert f.id in admin_ids
        assert f.id not in insight_ids

    def test_admin_sees_child_dir_in_admin_bucket(
        self,
        client,
        db_session,
    ):
        """Admin sees child dir in admin bucket when no insight permission."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        parent = _make_dir(db_session, "ParentAdmin")
        _make_dir(
            db_session,
            "ChildNoPerms",
            parent_id=parent.id,
            perms=["vbn_fu"],
        )
        resp = client.get(
            f"/api/archive/dirs/{parent.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        content = resp.json()["content"]
        admin_names = [x["name"] for x in content["subdirs"]["admin"]]
        assert "ChildNoPerms" in admin_names


# ------------------------------------------------------------------ #
# 404 errors for non-existent resources
# ------------------------------------------------------------------ #


class TestNotFoundErrors:
    def test_get_dir_detail_not_found(
        self,
        client,
        db_session,
    ):
        """GET /dirs/99999 returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.get("/api/archive/dirs/99999", headers=headers)
        assert resp.status_code == 404

    def test_create_dir_with_invalid_parent(
        self,
        client,
        db_session,
    ):
        """POST /dirs with non-existent parentId returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.post(
            "/api/archive/dirs",
            json={
                "name": "Orphan",
                "parentId": 99999,
                "permissions": [],
                "recursive_permissions": False,
            },
            headers=headers,
        )
        assert resp.status_code == 404

    def test_update_dir_not_found(
        self,
        client,
        db_session,
    ):
        """PUT /dirs/99999 returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.put(
            "/api/archive/dirs/99999",
            json={
                "name": "Ghost",
                "permissions": [],
                "recursive_permissions": False,
            },
            headers=headers,
        )
        assert resp.status_code == 404

    def test_delete_dir_not_found(
        self,
        client,
        db_session,
    ):
        """DELETE /dirs/99999 returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.delete(
            "/api/archive/dirs/99999",
            headers=headers,
        )
        assert resp.status_code == 404

    def test_restore_dir_not_found(
        self,
        client,
        db_session,
    ):
        """PATCH /dirs/99999/restore returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.patch(
            "/api/archive/dirs/99999/restore",
            headers=headers,
        )
        assert resp.status_code == 404

    def test_get_file_detail_not_found(
        self,
        client,
        db_session,
    ):
        """GET /files/99999 returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.get(
            "/api/archive/files/99999",
            headers=headers,
        )
        assert resp.status_code == 404

    def test_update_file_not_found(
        self,
        client,
        db_session,
    ):
        """PUT /files/99999 returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.put(
            "/api/archive/files/99999",
            json={"description": "nope"},
            headers=headers,
        )
        assert resp.status_code == 404

    def test_delete_file_not_found(
        self,
        client,
        db_session,
    ):
        """DELETE /files/99999 returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.delete(
            "/api/archive/files/99999",
            headers=headers,
        )
        assert resp.status_code == 404

    def test_restore_file_not_found(
        self,
        client,
        db_session,
    ):
        """PATCH /files/99999/restore returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.patch(
            "/api/archive/files/99999/restore",
            headers=headers,
        )
        assert resp.status_code == 404

    def test_presigned_url_file_not_found(
        self,
        client,
        db_session,
    ):
        """GET /files/99999/url returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.get(
            "/api/archive/files/99999/url",
            headers=headers,
        )
        assert resp.status_code == 404

    def test_create_comment_file_not_found(
        self,
        client,
        db_session,
    ):
        """POST /files/99999/comments returns 404."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        resp = client.post(
            "/api/archive/files/99999/comments",
            json={"content": "Comment on ghost file"},
            headers=headers,
        )
        assert resp.status_code == 404

    def test_delete_comment_wrong_file_id(
        self,
        client,
        db_session,
    ):
        """DELETE comment with mismatched file_id returns 404."""
        _seed(db_session)
        headers, admin = _login_admin(db_session, client)
        f = _make_file(db_session, desc="real-file")
        c = ArchiveFileComment(
            archive_file_id=f.id,
            content="Attached to real file",
            created_by=admin.id,
            created_at=_now(),
        )
        db_session.add(c)
        db_session.commit()
        # Delete comment using a different file ID
        resp = client.delete(
            f"/api/archive/files/99999/comments/{c.id}",
            headers=headers,
        )
        assert resp.status_code == 404


# ------------------------------------------------------------------ #
# update_dir happy path (lines 440-456)
# ------------------------------------------------------------------ #


class TestUpdateDir:
    def test_update_dir_success(
        self,
        client,
        db_session,
    ):
        """PUT /dirs/{id} updates name, description and permissions."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "OldName", perms=["vbw_fu"])
        resp = client.put(
            f"/api/archive/dirs/{d.id}",
            json={
                "name": "NewName",
                "description": "Updated description",
                "permissions": ["vbn_bi"],
                "recursive_permissions": True,
            },
            headers=headers,
        )
        assert resp.status_code == 200
        db_session.expire_all()
        refreshed = db_session.get(ArchiveDir, d.id)
        assert refreshed.name == "NewName"
        assert refreshed.description == "Updated description"
        assert refreshed.recursive_permissions is True

    def test_update_dir_requires_admin(
        self,
        client,
        db_session,
    ):
        """Non-admin cannot update a dir (403)."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        d = _make_dir(db_session, "Locked", perms=["vbw_fu"])
        resp = client.put(
            f"/api/archive/dirs/{d.id}",
            json={
                "name": "Changed",
                "permissions": [],
                "recursive_permissions": False,
            },
            headers=headers,
        )
        assert resp.status_code == 403


# ------------------------------------------------------------------ #
# Move/Receive validation (lines 508, 521, 523, 546, 580)
# ------------------------------------------------------------------ #


class TestMoveReceiveEdgeCases:
    def test_receive_to_nonexistent_target_dir(
        self,
        client,
        db_session,
    ):
        """Moving to a non-existent target dir returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        f = _make_file(db_session, dir_id=0)
        resp = client.post(
            "/api/archive/dirs/99999/receive",
            json={
                "type": "file",
                "ids": [f.id],
                "action": "move",
            },
            headers=headers,
        )
        assert resp.status_code == 404

    def test_move_dir_into_itself(
        self,
        client,
        db_session,
    ):
        """Moving a dir into itself returns 422."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "SelfTarget")
        resp = client.post(
            f"/api/archive/dirs/{d.id}/receive",
            json={
                "type": "dir",
                "ids": [d.id],
                "action": "move",
            },
            headers=headers,
        )
        assert resp.status_code == 422

    def test_move_nonexistent_dir_is_skipped(
        self,
        client,
        db_session,
    ):
        """receive_items silently skips non-existent dir IDs."""
        _seed(db_session)
        _, admin = _login_admin(db_session, client)
        target = _make_dir(db_session, "Target")
        # Should not raise
        receive_items(db_session, target.id, "dir", [99999], admin)

    def test_move_nonexistent_file_ids_skipped(
        self,
        client,
        db_session,
    ):
        """receive_items silently skips non-existent file IDs."""
        _seed(db_session)
        _, admin = _login_admin(db_session, client)
        # Should not raise
        receive_items(db_session, 0, "file", [99999, 88888], admin)

    def test_is_descendant_broken_chain(
        self,
        db_session,
    ):
        """_is_descendant returns False when a dir in the chain is missing."""
        _seed(db_session)
        d = _make_dir(db_session, "Orphan")
        # Point parent to a non-existent ID
        d.archive_dir_id = 99999
        db_session.commit()
        result = _is_descendant(db_session, d.id, 1)
        assert result is False


# ------------------------------------------------------------------ #
# Download with no version (line 750)
# ------------------------------------------------------------------ #


class TestDownloadNoVersion:
    def test_download_file_without_version_returns_404(
        self,
        client,
        db_session,
    ):
        """Downloading a file with no store item returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        f = _make_file_no_version(db_session)
        resp = client.get(
            f"/api/archive/files/{f.id}/download",
            headers=headers,
        )
        assert resp.status_code == 404
        assert "Keine Version" in resp.json()["detail"]

    def test_presigned_url_file_without_version_returns_404(
        self,
        client,
        db_session,
    ):
        """Presigned URL for a file with no store item returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        f = _make_file_no_version(db_session)
        resp = client.get(
            f"/api/archive/files/{f.id}/url",
            headers=headers,
        )
        assert resp.status_code == 404
        assert "Keine Version" in resp.json()["detail"]


# ------------------------------------------------------------------ #
# Thumbnail download and caching (lines 762-765, 778-780, 852, 858-867)
# ------------------------------------------------------------------ #


class TestThumbnailDownload:
    def test_download_thumbnail_for_image(
        self,
        client,
        db_session,
        mock_s3,
    ):
        """Downloading with size=sm for image creates and returns a thumbnail."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        f = _make_file(db_session, dir_id=0, desc="img-thumb")
        item = _active_store_item(f)
        # Upload real JPEG data to S3 for the store item
        jpeg_data = _make_jpeg_bytes(200, 200)
        key = f"archive/store/{item.sha256_hash}"
        mock_s3.upload(key, jpeg_data, "image/jpeg")

        resp = client.get(
            f"/api/archive/files/{f.id}/download/sm",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"

    def test_download_thumbnail_uses_cache(
        self,
        client,
        db_session,
        mock_s3,
    ):
        """Second thumbnail request uses cached version."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        f = _make_file(
            db_session,
            dir_id=0,
            desc="cached-thumb",
            hash_suffix="cache",
        )
        item = _active_store_item(f)
        # Pre-populate the cache (versioned key: bumping THUMBNAIL_CACHE_VERSION
        # invalidates stale entries, e.g. after the EXIF-orientation fix)
        cache_key = (
            f"archive/cache/{item.sha256_hash}.{THUMBNAIL_CACHE_VERSION}.thumb_sm"
        )
        cached_jpeg = _make_jpeg_bytes(50, 50)
        mock_s3.upload(cache_key, cached_jpeg, "image/jpeg")

        resp = client.get(
            f"/api/archive/files/{f.id}/download/sm",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"

    def test_stale_unversioned_cache_entry_is_ignored(
        self,
        client,
        db_session,
        mock_s3,
    ):
        """A cache entry from a previous THUMBNAIL_CACHE_VERSION (e.g. still
        holding a wrongly-oriented pre-fix thumbnail) must not be served -
        it has to be regenerated from the source under the new cache key."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        f = _make_file(
            db_session,
            dir_id=0,
            desc="stale-cache",
            hash_suffix="stalecache",
        )
        item = _active_store_item(f)
        # Old, unversioned cache key from before THUMBNAIL_CACHE_VERSION existed
        stale_key = f"archive/cache/{item.sha256_hash}.thumb_sm"
        mock_s3.upload(stale_key, _make_jpeg_bytes(50, 50), "image/jpeg")
        # Real source so a fresh, correctly-versioned thumbnail can be built
        source_key = f"archive/store/{item.sha256_hash}"
        mock_s3.upload(source_key, _make_jpeg_bytes(200, 200), "image/jpeg")

        resp = client.get(
            f"/api/archive/files/{f.id}/download/sm",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"
        versioned_key = (
            f"archive/cache/{item.sha256_hash}.{THUMBNAIL_CACHE_VERSION}.thumb_sm"
        )
        assert mock_s3.exists(versioned_key)

    def test_thumbnail_returns_none_when_source_missing(
        self,
        db_session,
        mock_s3,
    ):
        """_get_or_create_thumbnail returns None when source not in S3."""
        _seed(db_session)
        f = _make_file(
            db_session,
            dir_id=0,
            desc="no-source",
            hash_suffix="nosource",
        )
        item = _active_store_item(f)
        result = _get_or_create_thumbnail(item, "sm", mock_s3)
        assert result is None

    def test_thumbnail_returns_none_on_corrupt_image(
        self,
        db_session,
        mock_s3,
    ):
        """_get_or_create_thumbnail returns None for corrupt image data."""
        _seed(db_session)
        f = _make_file(
            db_session,
            dir_id=0,
            desc="corrupt",
            hash_suffix="corrupt",
        )
        item = _active_store_item(f)
        # Upload garbage bytes that are not a valid image
        key = f"archive/store/{item.sha256_hash}"
        mock_s3.upload(key, b"not-an-image-at-all", "image/jpeg")

        result = _get_or_create_thumbnail(item, "sm", mock_s3)
        assert result is None

    def test_serve_thumbnail_returns_none_when_no_thumb_data(
        self,
        db_session,
        mock_s3,
    ):
        """_serve_thumbnail returns None when thumbnail cannot be created."""
        _seed(db_session)
        f = _make_file(
            db_session,
            dir_id=0,
            desc="no-thumb",
            hash_suffix="nothumb",
        )
        item = _active_store_item(f)
        # No source in S3 -> thumbnail creation returns None
        result = _serve_thumbnail(item, "sm", mock_s3)
        assert result is None

    def test_download_falls_through_when_thumbnail_fails(
        self,
        client,
        db_session,
        mock_s3,
    ):
        """Download with valid thumb size falls through to original
        when thumbnail creation fails (non-image or corrupt)."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        # Create a PDF file (not an image) so thumbnail is skipped
        f = _make_file(
            db_session,
            dir_id=0,
            desc="pdf-file",
            extension="pdf",
            mime_type="application/pdf",
            hash_suffix="pdf",
        )
        item = _active_store_item(f)
        pdf_content = _valid_file_content(3)
        key = f"archive/store/{item.sha256_hash}"
        mock_s3.upload(key, pdf_content, "application/pdf")

        resp = client.get(
            f"/api/archive/files/{f.id}/download/sm",
            headers=headers,
        )
        # Falls through to regular download since PDF is not is_image
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"


# ------------------------------------------------------------------ #
# Download S3 error (lines 791-793)
# ------------------------------------------------------------------ #


class TestDownloadS3Error:
    def test_download_s3_client_error_returns_404(
        self,
        client,
        db_session,
    ):
        """Download returns 404 when S3 storage raises ClientError."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        f = _make_file(
            db_session,
            dir_id=0,
            desc="missing-in-s3",
            hash_suffix="s3err",
        )
        # File exists in DB but NOT in S3 storage -> ClientError
        resp = client.get(
            f"/api/archive/files/{f.id}/download",
            headers=headers,
        )
        assert resp.status_code == 404
        assert "Speicher" in resp.json()["detail"]


# ------------------------------------------------------------------ #
# Upload edge case: no extension (line 912)
# ------------------------------------------------------------------ #


class TestUploadNoExtension:
    def test_upload_file_without_extension(
        self,
        client,
        db_session,
    ):
        """Uploading a file without a dot in the name returns 422."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        content = _valid_file_content(3)
        resp = client.post(
            "/api/archive/upload",
            files={
                "file": ("noextension", content, "application/octet-stream"),
            },
            data={"description": "File without extension"},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_upload_file_with_empty_filename(
        self,
        client,
        db_session,
    ):
        """Uploading a file with no filename returns 422."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        content = _valid_file_content(3)
        resp = client.post(
            "/api/archive/upload",
            files={
                "file": ("", content, "application/octet-stream"),
            },
            data={"description": "Empty filename upload"},
            headers=headers,
        )
        assert resp.status_code == 422


# ------------------------------------------------------------------ #
# Search (lines 1059-1060, 1068-1148)
# ------------------------------------------------------------------ #


class TestSearch:
    def test_search_finds_dir_by_name(
        self,
        client,
        db_session,
    ):
        """Search returns matching directories (non-root only)."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        parent = _make_dir(db_session, "TopLevel")
        _make_dir(
            db_session,
            "Vereinsfotos",
            parent_id=parent.id,
            perms=["vbw_bi"],
        )
        resp = client.get(
            "/api/archive/search?q=Vereins",
            headers=headers,
        )
        assert resp.status_code == 200
        results = resp.json()
        dir_results = [r for r in results if r["type"] == "dir"]
        assert len(dir_results) >= 1
        assert dir_results[0]["name"] == "Vereinsfotos"

    def test_search_finds_file_by_name(
        self,
        client,
        db_session,
    ):
        """Search returns matching files."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "Docs")
        _make_file(
            db_session,
            dir_id=d.id,
            desc="testfile description",
            hash_suffix="search",
        )
        resp = client.get(
            "/api/archive/search?q=testfile",
            headers=headers,
        )
        assert resp.status_code == 200
        results = resp.json()
        file_results = [r for r in results if r["type"] == "file"]
        assert len(file_results) >= 1

    def test_search_respects_permissions_for_normal_user(
        self,
        client,
        db_session,
    ):
        """Normal user only sees dirs they have insight permission for."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        parent = _make_dir(db_session, "Container")
        _make_dir(
            db_session,
            "SearchVisible",
            parent_id=parent.id,
            perms=["vbw_fu"],
        )
        _make_dir(
            db_session,
            "SearchHidden",
            parent_id=parent.id,
            perms=["vbn_bi"],
        )
        resp = client.get(
            "/api/archive/search?q=Search",
            headers=headers,
        )
        assert resp.status_code == 200
        results = resp.json()
        names = [r["name"] for r in results]
        assert "SearchVisible" in names
        assert "SearchHidden" not in names

    def test_search_no_results(
        self,
        client,
        db_session,
    ):
        """Search with no matching term returns empty list."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.get(
            "/api/archive/search?q=xyznonexistent",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_search_finds_file_by_description(
        self,
        client,
        db_session,
    ):
        """Search matches on file description as well."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "DescDir")
        _make_file(
            db_session,
            dir_id=d.id,
            desc="Festkommers Protokoll",
            hash_suffix="desc-search",
        )
        resp = client.get(
            "/api/archive/search?q=Festkommers",
            headers=headers,
        )
        assert resp.status_code == 200
        results = resp.json()
        file_results = [r for r in results if r["type"] == "file"]
        assert len(file_results) >= 1
        assert file_results[0]["description"] == "Festkommers Protokoll"

    def test_search_file_permission_filtering(
        self,
        client,
        db_session,
    ):
        """Normal user cannot see files in dirs without insight permission."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        restricted = _make_dir(
            db_session,
            "RestrictedDir",
            perms=["vbn_bi"],
        )
        _make_file(
            db_session,
            dir_id=restricted.id,
            desc="SecretSearchFile",
            hash_suffix="perm-search",
        )
        resp = client.get(
            "/api/archive/search?q=SecretSearch",
            headers=headers,
        )
        assert resp.status_code == 200
        results = resp.json()
        file_results = [r for r in results if r["type"] == "file"]
        assert len(file_results) == 0

    def test_search_file_at_root_has_archiv_path(
        self,
        client,
        db_session,
    ):
        """Files at root (dir_id=0) show 'Archiv' as path in search."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        _make_file(
            db_session,
            dir_id=0,
            desc="RootSearchFile",
            hash_suffix="root-search",
        )
        resp = client.get(
            "/api/archive/search?q=RootSearch",
            headers=headers,
        )
        assert resp.status_code == 200
        results = resp.json()
        file_results = [r for r in results if r["type"] == "file"]
        assert len(file_results) >= 1
        assert file_results[0]["path"] == "Archiv"

    def test_search_requires_min_3_chars(
        self,
        client,
        db_session,
    ):
        """Search query must be at least 3 characters (FastAPI validation)."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.get(
            "/api/archive/search?q=ab",
            headers=headers,
        )
        assert resp.status_code == 422


# ------------------------------------------------------------------ #
# Presigned URL with thumbnail size (line 823 + thumbnail path)
# ------------------------------------------------------------------ #


class TestPresignedUrlThumbnail:
    def test_presigned_url_with_thumb_size_for_image(
        self,
        client,
        db_session,
        mock_s3,
    ):
        """Presigned URL with size param creates thumbnail and returns cache URL."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        f = _make_file(
            db_session,
            dir_id=0,
            desc="url-thumb",
            hash_suffix="url-thumb",
        )
        item = _active_store_item(f)
        jpeg_data = _make_jpeg_bytes(200, 200)
        key = f"archive/store/{item.sha256_hash}"
        mock_s3.upload(key, jpeg_data, "image/jpeg")

        resp = client.get(
            f"/api/archive/files/{f.id}/url/sm",
            headers=headers,
        )
        assert resp.status_code == 200
        url = resp.json()["url"]
        # Thumbnail cache key should appear in URL
        assert "thumb_sm" in url


# ------------------------------------------------------------------ #
# Pydantic schema validators (archive.py lines 140-218)
# ------------------------------------------------------------------ #


class TestArchiveSchemaValidators:
    def test_dir_save_name_too_short(self):
        with pytest.raises(Exception, match="3-64 Zeichen"):
            DirSaveRequest(name="ab")

    def test_dir_save_name_too_long(self):
        with pytest.raises(Exception, match="3-64 Zeichen"):
            DirSaveRequest(name="x" * 65)

    def test_dir_save_name_valid(self):
        req = DirSaveRequest(name="  Valid Name  ")
        assert req.name == "Valid Name"

    def test_dir_save_description_too_long(self):
        with pytest.raises(Exception, match="128 Zeichen"):
            DirSaveRequest(name="ValidName", description="x" * 129)

    def test_dir_save_description_at_limit(self):
        req = DirSaveRequest(name="ValidName", description="x" * 128)
        assert len(req.description) == 128

    def test_dir_save_invalid_permission_format(self):
        with pytest.raises(Exception, match="Ungültiges Format"):
            DirSaveRequest(name="ValidName", permissions=["invalid"])

    def test_dir_save_valid_permission(self):
        req = DirSaveRequest(name="ValidName", permissions=["vbw_bi"])
        assert req.permissions == ["vbw_bi"]

    def test_dir_receive_invalid_type(self):
        with pytest.raises(Exception, match=r"dir.*file"):
            DirReceiveRequest(type="invalid", ids=[1])

    def test_dir_receive_invalid_action(self):
        with pytest.raises(Exception, match="move"):
            DirReceiveRequest(type="dir", ids=[1], action="copy")

    def test_dir_receive_valid(self):
        req = DirReceiveRequest(type="file", ids=[1, 2], action="move")
        assert req.type == "file"

    def test_file_update_description_too_long(self):
        with pytest.raises(Exception, match="128 Zeichen"):
            FileUpdateRequest(description="x" * 129)

    def test_file_update_description_valid(self):
        req = FileUpdateRequest(description="Short desc")
        assert req.description == "Short desc"

    def test_comment_content_too_short(self):
        with pytest.raises(Exception, match="5-1000 Zeichen"):
            CommentCreateRequest(content="ab")

    def test_comment_content_too_long(self):
        with pytest.raises(Exception, match="5-1000 Zeichen"):
            CommentCreateRequest(content="x" * 1001)

    def test_comment_content_valid(self):
        req = CommentCreateRequest(content="Valid comment text")
        assert req.content == "Valid comment text"

    def test_comment_content_strips_whitespace(self):
        req = CommentCreateRequest(content="  Valid comment text  ")
        assert req.content == "Valid comment text"


class TestGetUnsortedUploadCount:
    def test_counts_org_wide_not_per_user(self, db_session):
        """Unlike get_unfiled_uploads(), this must NOT filter by uploader -
        it's an admin-facing, org-wide count for the weekly health check."""
        _make_file(db_session, dir_id=0, hash_suffix="a")
        _make_file(db_session, dir_id=0, hash_suffix="b")

        assert get_unsorted_upload_count(db_session) == 2

    def test_excludes_filed_files(self, db_session):
        unsorted = _make_file(db_session, dir_id=0, hash_suffix="c")
        filed = _make_file(db_session, dir_id=0, hash_suffix="d")
        filed.archive_dir_id = 5
        db_session.commit()

        assert get_unsorted_upload_count(db_session) == 1
        assert unsorted.archive_dir_id == 0

    def test_zero_when_none_unsorted(self, db_session):
        filed = _make_file(db_session, dir_id=0, hash_suffix="e")
        filed.archive_dir_id = 7
        db_session.commit()

        assert get_unsorted_upload_count(db_session) == 0
