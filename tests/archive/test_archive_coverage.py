"""Tests to increase coverage for app/services/archive_service.py.

Targets uncovered lines: 160-162, 256-258, 270-275, 296, 330-331, 344,
361, 408-410, 440-456, 467, 494, 508, 521, 523, 546, 580, 631, 690,
707, 723, 750, 762-765, 778-780, 791-793, 823, 852, 858-867, 912,
1016, 1042, 1059-1060, 1068-1148.
"""

import os
import uuid
from datetime import UTC, date, datetime
from io import BytesIO

import bcrypt
import pytest
from PIL import Image as PILImage

from app.core.storage import THUMBNAIL_CACHE_VERSION
from app.models.archive_dir import ArchiveDir
from app.models.archive_file import ArchiveFile
from app.models.archive_file_comment import ArchiveFileComment
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
    _get_or_create_thumbnail,
    _is_descendant,
    get_archive_stats,
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
            member_id=m.id_uuid,
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
                archive_dir_id=d.id_uuid,
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
        archive_store_item_id=item.id,
        created_at=now,
        updated_at=now,
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
        assert str(f.id) in admin_ids

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
        assert str(f.id) in trashed_ids

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
        body = resp.json()
        content = body["content"]
        admin_ids = [x["id"] for x in content["files"]["admin"]]
        insight_ids = [x["id"] for x in content["files"]["insight"]]
        assert str(f.id) in admin_ids
        assert str(f.id) not in insight_ids
        # A real subdirectory never carries the root-only archive stats -
        # keeps the "stats" field's shape symmetric between the two
        # response variants for the frontend.
        assert body["stats"] is None

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
        """GET /files/<random-uuid> returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.get(
            f"/api/archive/files/{uuid.uuid4()}",
            headers=headers,
        )
        assert resp.status_code == 404

    def test_update_file_not_found(
        self,
        client,
        db_session,
    ):
        """PUT /files/<random-uuid> returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.put(
            f"/api/archive/files/{uuid.uuid4()}",
            json={"description": "nope"},
            headers=headers,
        )
        assert resp.status_code == 404

    def test_delete_file_not_found(
        self,
        client,
        db_session,
    ):
        """DELETE /files/<random-uuid> returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.delete(
            f"/api/archive/files/{uuid.uuid4()}",
            headers=headers,
        )
        assert resp.status_code == 404

    def test_restore_file_not_found(
        self,
        client,
        db_session,
    ):
        """PATCH /files/<random-uuid>/restore returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.patch(
            f"/api/archive/files/{uuid.uuid4()}/restore",
            headers=headers,
        )
        assert resp.status_code == 404

    def test_presigned_url_file_not_found(
        self,
        client,
        db_session,
    ):
        """GET /files/<random-uuid>/url returns 404."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.get(
            f"/api/archive/files/{uuid.uuid4()}/url",
            headers=headers,
        )
        assert resp.status_code == 404

    def test_create_comment_file_not_found(
        self,
        client,
        db_session,
    ):
        """POST /files/<random-uuid>/comments returns 404."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        resp = client.post(
            f"/api/archive/files/{uuid.uuid4()}/comments",
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
            created_by=admin.id_uuid,
            created_at=_now(),
        )
        db_session.add(c)
        db_session.commit()
        # Delete comment using a different file ID
        resp = client.delete(
            f"/api/archive/files/{uuid.uuid4()}/comments/{c.id}",
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
                "ids": [str(f.id)],
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
        receive_items(db_session, 0, "file", [uuid.uuid4(), uuid.uuid4()], admin)

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
# Thumbnail caching (_get_or_create_thumbnail) - tested directly rather
# than through an HTTP endpoint, since this logic is shared by whichever
# delivery endpoint requests it (currently only /url/{size})
# ------------------------------------------------------------------ #


class TestThumbnailCaching:
    def test_uses_cached_version_without_regenerating(
        self,
        db_session,
        mock_s3,
    ):
        """A pre-existing, correctly-versioned cache entry is returned as-is,
        with no source image needed (proves the cache-hit path never touches
        the source key)."""
        _seed(db_session)
        f = _make_file(
            db_session,
            dir_id=0,
            desc="cached-thumb",
            hash_suffix="cache",
        )
        item = f.store_item
        cache_key = (
            f"archive/cache/{item.sha256_hash}.{THUMBNAIL_CACHE_VERSION}.thumb_sm"
        )
        cached_jpeg = _make_jpeg_bytes(50, 50)
        mock_s3.upload(cache_key, cached_jpeg, "image/jpeg")

        result = _get_or_create_thumbnail(item.sha256_hash, "sm", mock_s3)
        assert result == cached_jpeg

    def test_stale_unversioned_cache_entry_is_ignored(
        self,
        db_session,
        mock_s3,
    ):
        """A cache entry from a previous THUMBNAIL_CACHE_VERSION (e.g. still
        holding a wrongly-oriented pre-fix thumbnail) must not be served -
        it has to be regenerated from the source under the new cache key."""
        _seed(db_session)
        f = _make_file(
            db_session,
            dir_id=0,
            desc="stale-cache",
            hash_suffix="stalecache",
        )
        item = f.store_item
        # Old, unversioned cache key from before THUMBNAIL_CACHE_VERSION existed
        stale_key = f"archive/cache/{item.sha256_hash}.thumb_sm"
        mock_s3.upload(stale_key, _make_jpeg_bytes(50, 50), "image/jpeg")
        # Real source so a fresh, correctly-versioned thumbnail can be built
        source_key = f"archive/store/{item.sha256_hash}"
        mock_s3.upload(source_key, _make_jpeg_bytes(200, 200), "image/jpeg")

        result = _get_or_create_thumbnail(item.sha256_hash, "sm", mock_s3)
        assert result is not None
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
        item = f.store_item
        result = _get_or_create_thumbnail(item.sha256_hash, "sm", mock_s3)
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
        item = f.store_item
        # Upload garbage bytes that are not a valid image
        key = f"archive/store/{item.sha256_hash}"
        mock_s3.upload(key, b"not-an-image-at-all", "image/jpeg")

        result = _get_or_create_thumbnail(item.sha256_hash, "sm", mock_s3)
        assert result is None

    def test_presigned_url_falls_through_when_not_an_image(
        self,
        client,
        db_session,
        mock_s3,
    ):
        """Presigned URL with a valid thumb size falls through to the
        original file's key when the file is not an image (thumbnail
        generation is skipped entirely, not attempted-and-failed)."""
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
        item = f.store_item
        pdf_content = _valid_file_content(3)
        key = f"archive/store/{item.sha256_hash}"
        mock_s3.upload(key, pdf_content, "application/pdf")

        resp = client.get(
            f"/api/archive/files/{f.id}/url/sm",
            headers=headers,
        )
        assert resp.status_code == 200
        # Falls through to the original file's URL, not a thumbnail cache key
        assert "thumb_sm" not in resp.json()["url"]


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

    def test_search_finds_file_by_extension(
        self,
        client,
        db_session,
    ):
        """Search matches on the file's extension too, not just its base
        name (which never contains the extension - upload_file() splits
        them apart) or its description."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "ExtDir")
        _make_file(
            db_session,
            dir_id=d.id,
            desc="unrelated description",
            extension="txt",
            hash_suffix="ext-search",
        )
        resp = client.get(
            "/api/archive/search?q=txt",
            headers=headers,
        )
        assert resp.status_code == 200
        file_results = [r for r in resp.json() if r["type"] == "file"]
        assert len(file_results) >= 1

    def test_search_finds_file_by_comment_content(
        self,
        client,
        db_session,
    ):
        """Search matches a non-deleted comment's content too - and a file
        with several matching comments must still appear only once."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "CommentDir")
        f = _make_file(
            db_session,
            dir_id=d.id,
            desc="unrelated description",
            hash_suffix="comment-search",
        )
        now = _now()
        db_session.add_all(
            [
                ArchiveFileComment(
                    archive_file_id=f.id,
                    content="Festumzug erwähnt hier",
                    created_at=now,
                    updated_at=now,
                ),
                ArchiveFileComment(
                    archive_file_id=f.id,
                    content="Festumzug auch nochmal hier",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        db_session.commit()

        resp = client.get(
            "/api/archive/search?q=Festumzug",
            headers=headers,
        )

        assert resp.status_code == 200
        file_results = [r for r in resp.json() if r["type"] == "file"]
        assert len(file_results) == 1
        assert file_results[0]["id"] == str(f.id)

    def test_search_ignores_soft_deleted_comment_content(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "TrashedCommentDir")
        f = _make_file(
            db_session,
            dir_id=d.id,
            desc="unrelated description",
            hash_suffix="trashed-comment-search",
        )
        now = _now()
        db_session.add(
            ArchiveFileComment(
                archive_file_id=f.id,
                content="Sonderwort löschprobe",
                created_at=now,
                updated_at=now,
                deleted_at=now,
            )
        )
        db_session.commit()

        resp = client.get(
            "/api/archive/search?q=löschprobe",
            headers=headers,
        )

        assert resp.status_code == 200
        file_results = [r for r in resp.json() if r["type"] == "file"]
        assert str(f.id) not in [r["id"] for r in file_results]

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

    def test_search_finds_top_level_dir_by_name(
        self,
        client,
        db_session,
    ):
        """Regression: a directory directly under root (archive_dir_id=0,
        a sentinel with no real row — see get_root_content()) must still be
        searchable. An earlier `archive_dir_id != 0` filter excluded every
        top-level directory from search results, not just the nonexistent
        root row it was meant to guard against."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        _make_dir(db_session, "Digitalisierte Archivstube")
        resp = client.get(
            "/api/archive/search?q=Archivstube",
            headers=headers,
        )
        assert resp.status_code == 200
        results = resp.json()
        dir_results = [r for r in results if r["type"] == "dir"]
        assert len(dir_results) == 1
        assert dir_results[0]["name"] == "Digitalisierte Archivstube"

    def test_search_hides_unsorted_root_files_from_normal_user(
        self,
        client,
        db_session,
    ):
        """Regression: files at root (dir_id=0, unsorted uploads) have no
        resolvable parent directory. The permission check used to be
        `if parent and not admin and not can_insight(...): continue` —
        with parent None, that short-circuited to False and skipped the
        check entirely, showing unsorted uploads to every authenticated
        user. get_root_content() only ever exposes them to archiveAdmin,
        so search must match that."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        _make_file(
            db_session,
            dir_id=0,
            desc="UnsortedSearchFile",
            hash_suffix="unsorted-perm",
        )
        resp = client.get(
            "/api/archive/search?q=UnsortedSearch",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_search_requires_min_2_chars(
        self,
        client,
        db_session,
    ):
        """Search query must be at least 2 characters (FastAPI validation)
        - lower than most other searches, since the archive has many
        meaningful 2-letter abbreviations (BC/MC/FC/DC committees)."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.get(
            "/api/archive/search?q=a",
            headers=headers,
        )
        assert resp.status_code == 422

    def test_search_accepts_2_char_query(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        _make_dir(db_session, "BC-Protokolle")
        resp = client.get(
            "/api/archive/search?q=BC",
            headers=headers,
        )
        assert resp.status_code == 200
        names = [r["name"] for r in resp.json()]
        assert "BC-Protokolle" in names

    def test_search_prefix_matches_compound_word(
        self,
        client,
        db_session,
    ):
        """Full-text search (Stage 1): "Kassa" must find a dir named
        "Kassabericht 2024" via prefix matching (search_vector's ':*'
        suffix, see build_prefix_tsquery_text in search_utils.py) -
        Postgres's 'german' text search config stems suffixes but never
        splits compound words, so without prefix matching this dir would
        be unreachable by a plain websearch_to_tsquery("Kassa") the same
        way it already was unreachable before Stage 1's tsvector search
        even existed."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        _make_dir(db_session, "Kassabericht 2024")

        resp = client.get("/api/archive/search?q=Kassa", headers=headers)

        assert resp.status_code == 200
        names = [r["name"] for r in resp.json()]
        assert "Kassabericht 2024" in names

    def test_search_ranks_name_match_above_comment_match(
        self,
        client,
        db_session,
    ):
        """Full-text search (Stage 1): a file whose *name* matches the
        query ranks above one that only matches via an incidental comment
        mention - name is weight 'A', comment content is the lowest
        weight 'D' (see the migration/model docstrings)."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "RankDir")

        comment_only_hit = _make_file(
            db_session,
            dir_id=d.id,
            desc="vollkommen unrelated",
            hash_suffix="rank-comment-only",
        )
        now = _now()
        db_session.add(
            ArchiveFileComment(
                archive_file_id=comment_only_hit.id,
                content="Vindstoff wird hier nur beiläufig im Kommentar erwähnt",
                created_at=now,
                updated_at=now,
            )
        )
        db_session.commit()

        name_hit_item = ArchiveStoreItem(
            name="Vindstoff_Uebersicht",
            extension="pdf",
            mime_type="application/pdf",
            size=1000,
            sha256_hash=f"hash_{now.timestamp()}_rank-name-match",
            created_at=now,
            updated_at=now,
        )
        db_session.add(name_hit_item)
        db_session.flush()
        name_hit = ArchiveFile(
            archive_dir_id=d.id,
            description="unrelated too",
            archive_store_item_id=name_hit_item.id,
            created_at=now,
            updated_at=now,
        )
        db_session.add(name_hit)
        db_session.commit()

        resp = client.get("/api/archive/search?q=Vindstoff", headers=headers)

        assert resp.status_code == 200
        file_results = [r for r in resp.json() if r["type"] == "file"]
        ids_in_order = [r["id"] for r in file_results]
        assert ids_in_order.index(str(name_hit.id)) < ids_in_order.index(
            str(comment_only_hit.id)
        )

    def test_search_stopword_only_query_returns_empty(
        self,
        client,
        db_session,
    ):
        """A query consisting only of German stop words (e.g. "im") passes
        the 2-char length check but produces no real lexemes at all -
        websearch_to_tsquery() reduces it to an empty tsquery, and
        build_prefix_tsquery_text() (search_utils.py) must treat that the
        same as "no results" instead of erroring or (worse) matching
        everything.

        This also locks in the Stage 2 (pg_trgm) length gate
        (_FUZZY_MIN_QUERY_LENGTH): the seeded dir literally contains "im"
        as an isolated word, so word_similarity('im', ...) scores a
        coincidental 1.0 (verified empirically) - without the length
        gate, this exact scenario would start matching via the fuzzy
        fallback despite "im" not being a typo of anything."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        _make_dir(db_session, "Irgendein Verzeichnis im Archiv")

        resp = client.get("/api/archive/search?q=im", headers=headers)

        assert resp.status_code == 200
        assert resp.json() == []

    def test_search_fuzzy_fallback_finds_typo(
        self,
        client,
        db_session,
    ):
        """Full-text search (Stage 2, pg_trgm): a typo'd query that Stage 1
        cannot match via prefix search at all - "Festkomers" is not a
        prefix of the lexeme "festkommers" (mismatch at the 8th
        character) - is still found via word_similarity()'s substring
        fuzzy matching once Stage 1 returns nothing."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "TypoDir")
        _make_file(
            db_session,
            dir_id=d.id,
            desc="Einladung zum Festkommers",
            hash_suffix="fuzzy-typo",
        )

        resp = client.get(
            "/api/archive/search?q=Festkomers",
            headers=headers,
        )

        assert resp.status_code == 200
        file_results = [r for r in resp.json() if r["type"] == "file"]
        assert len(file_results) >= 1
        assert file_results[0]["description"] == "Einladung zum Festkommers"

    def test_search_fuzzy_fallback_respects_permissions(
        self,
        client,
        db_session,
    ):
        """The Stage 2 (pg_trgm) fallback shares _collect_file_hits() with
        Stage 1 - a normal user without insight permission must not see a
        fuzzy/typo match either, same as an exact one."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        restricted = _make_dir(
            db_session,
            "RestrictedTypoDir",
            perms=["vbn_bi"],
        )
        _make_file(
            db_session,
            dir_id=restricted.id,
            desc="Einladung zum Festkommers",
            hash_suffix="fuzzy-typo-perm",
        )

        resp = client.get(
            "/api/archive/search?q=Festkomers",
            headers=headers,
        )

        assert resp.status_code == 200
        assert resp.json() == []


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
        item = f.store_item
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

    def test_db_session_still_usable_after_presigned_url(
        self,
        client,
        db_session,
        mock_s3,
    ):
        """get_presigned_url() closes the DB session before the S3 call
        (regression test for the pool-exhaustion incident 2026-08-01). The
        session must still be safely reusable afterwards."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        f = _make_file(
            db_session,
            dir_id=0,
            desc="url-close-then-reuse",
            hash_suffix="url-reuse",
        )
        item = f.store_item
        jpeg_data = _make_jpeg_bytes(200, 200)
        key = f"archive/store/{item.sha256_hash}"
        mock_s3.upload(key, jpeg_data, "image/jpeg")

        resp = client.get(
            f"/api/archive/files/{f.id}/url/sm",
            headers=headers,
        )
        assert resp.status_code == 200
        assert db_session.get(ArchiveFile, f.id) is not None


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


class TestGetArchiveStats:
    def test_empty_archive_returns_zeroes(self, db_session):
        stats = get_archive_stats(db_session)

        assert stats == {
            "file_count": 0,
            "unique_object_count": 0,
            "dir_count": 0,
            "total_size": 0,
            "by_extension": [],
        }

    def test_counts_only_filed_files(self, db_session):
        """archive_dir_id == 0 is the "unsorted upload" sentinel, not a
        real directory - must not count towards file_count, mirroring
        get_unsorted_upload_count()'s inverse filter."""
        _make_dir(db_session, "dir-a")
        _make_file(db_session, dir_id=0, hash_suffix="unsorted")
        _make_file(db_session, dir_id=1, hash_suffix="filed-1")
        _make_file(db_session, dir_id=1, hash_suffix="filed-2")

        stats = get_archive_stats(db_session)

        assert stats["file_count"] == 2

    def test_excludes_soft_deleted_files(self, db_session):
        kept = _make_file(db_session, dir_id=1, hash_suffix="kept")
        trashed = _make_file(db_session, dir_id=1, hash_suffix="trashed")
        trashed.deleted_at = _now()
        db_session.commit()

        stats = get_archive_stats(db_session)

        assert stats["file_count"] == 1
        assert kept.deleted_at is None

    def test_excludes_soft_deleted_dirs(self, db_session):
        _make_dir(db_session, "dir-active")
        trashed_dir = _make_dir(db_session, "dir-trashed")
        trashed_dir.deleted_at = _now()
        db_session.commit()

        stats = get_archive_stats(db_session)

        assert stats["dir_count"] == 1

    def test_unique_object_count_and_total_size(self, db_session):
        """Each _make_file() call creates its own ArchiveStoreItem (size
        5000 fixed) - two files means two distinct store items here, no
        dedup involved."""
        _make_file(db_session, dir_id=1, hash_suffix="a")
        _make_file(db_session, dir_id=1, hash_suffix="b")

        stats = get_archive_stats(db_session)

        assert stats["unique_object_count"] == 2
        assert stats["total_size"] == 10000

    def test_unique_object_count_never_exceeds_file_count_when_files_share_an_object(
        self, db_session
    ):
        """Content-dedup means many files can point at one store item, but
        never the reverse - so unique_object_count must never exceed
        file_count. Two active files sharing one object here: 1 object,
        2 files."""
        first = _make_file(db_session, dir_id=1, hash_suffix="shared")
        db_session.add(
            ArchiveFile(
                archive_dir_id=1,
                description="same content, second file",
                archive_store_item_id=first.archive_store_item_id,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        db_session.commit()

        stats = get_archive_stats(db_session)

        assert stats["file_count"] == 2
        assert stats["unique_object_count"] == 1
        assert stats["unique_object_count"] <= stats["file_count"]

    def test_unique_object_count_excludes_objects_only_referenced_by_trashed_files(
        self, db_session
    ):
        """A store item still on disk but referenced only by a soft-deleted
        file must not inflate unique_object_count/total_size beyond what
        the (excluded) trashed file itself would justify - regression test
        for the scope mismatch this stat originally had."""
        trashed = _make_file(db_session, dir_id=1, hash_suffix="trash-only")
        trashed.deleted_at = _now()
        db_session.commit()

        stats = get_archive_stats(db_session)

        assert stats["file_count"] == 0
        assert stats["unique_object_count"] == 0
        assert stats["total_size"] == 0
        assert stats["by_extension"] == []

    def test_unique_object_count_excludes_objects_only_referenced_by_unsorted_files(
        self, db_session
    ):
        """Same scope-mismatch guard as above, for archive_dir_id == 0
        (unsorted upload) instead of a trashed file."""
        _make_file(db_session, dir_id=0, hash_suffix="unsorted-only")

        stats = get_archive_stats(db_session)

        assert stats["file_count"] == 0
        assert stats["unique_object_count"] == 0
        assert stats["total_size"] == 0

    def test_excludes_dir_nested_under_a_trashed_ancestor(self, db_session):
        """delete_dir() never cascades deleted_at onto children when
        trashing a non-empty directory - a child dir can be individually
        not-deleted while its parent is trashed, and is then only reachable
        via the trash, not normal navigation. Must not count in dir_count."""
        parent = _make_dir(db_session, "parent")
        child = _make_dir(db_session, "child", parent_id=parent.id)
        parent.deleted_at = _now()
        db_session.commit()

        stats = get_archive_stats(db_session)

        assert stats["dir_count"] == 0
        assert child.deleted_at is None

    def test_excludes_file_nested_under_a_trashed_ancestor(self, db_session):
        """Same as above, for a file sitting inside a trashed directory
        (the file's own deleted_at stays NULL - only its parent dir is
        trashed)."""
        parent = _make_dir(db_session, "parent")
        _make_file(db_session, dir_id=parent.id, hash_suffix="under-trash")
        parent.deleted_at = _now()
        db_session.commit()

        stats = get_archive_stats(db_session)

        assert stats["file_count"] == 0
        assert stats["unique_object_count"] == 0
        assert stats["total_size"] == 0

    def test_excludes_content_several_levels_under_a_trashed_ancestor(self, db_session):
        """The recursive CTE must walk the full ancestor chain, not just
        the immediate parent."""
        grandparent = _make_dir(db_session, "grandparent")
        parent = _make_dir(db_session, "parent", parent_id=grandparent.id)
        child = _make_dir(db_session, "child", parent_id=parent.id)
        _make_file(db_session, dir_id=child.id, hash_suffix="deep")
        grandparent.deleted_at = _now()
        db_session.commit()

        stats = get_archive_stats(db_session)

        assert stats["dir_count"] == 0
        assert stats["file_count"] == 0
        assert stats["unique_object_count"] == 0

    def test_does_not_exclude_a_sibling_branch_not_under_any_trashed_dir(
        self, db_session
    ):
        """Regression guard: the exclusion must be scoped to the actual
        trashed subtree, not accidentally suppress unrelated content."""
        trashed = _make_dir(db_session, "trashed-branch")
        _make_file(db_session, dir_id=trashed.id, hash_suffix="under-trash")
        trashed.deleted_at = _now()

        healthy = _make_dir(db_session, "healthy-branch")
        _make_file(db_session, dir_id=healthy.id, hash_suffix="healthy")
        db_session.commit()

        stats = get_archive_stats(db_session)

        assert stats["dir_count"] == 1
        assert stats["file_count"] == 1
        assert stats["unique_object_count"] == 1

    def test_by_extension_groups_and_sorts_by_count_desc(self, db_session):
        _make_file(db_session, dir_id=1, extension="jpg", hash_suffix="a")
        _make_file(db_session, dir_id=1, extension="jpg", hash_suffix="b")
        _make_file(db_session, dir_id=1, extension="jpg", hash_suffix="c")
        _make_file(db_session, dir_id=1, extension="pdf", hash_suffix="d")

        stats = get_archive_stats(db_session)

        assert stats["by_extension"] == [
            {"extension": "jpg", "count": 3, "size": 15000},
            {"extension": "pdf", "count": 1, "size": 5000},
        ]

    def test_query_count_does_not_scale_with_archive_size(
        self, db_session, count_queries
    ):
        """The four aggregate queries backing this stats block must stay
        flat regardless of how many dirs/files/store items exist."""
        _make_dir(db_session, "dir-small")
        _make_file(db_session, dir_id=1, extension="jpg", hash_suffix="small")

        with count_queries() as small:
            get_archive_stats(db_session)

        for i in range(10):
            _make_dir(db_session, f"dir-large-{i}", parent_id=1)
            _make_file(db_session, dir_id=1, extension="pdf", hash_suffix=f"large-{i}")

        with count_queries() as large:
            stats = get_archive_stats(db_session)

        assert stats["file_count"] == 11
        assert large.count == small.count
