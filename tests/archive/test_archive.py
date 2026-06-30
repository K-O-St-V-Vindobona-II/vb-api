"""Tests für das Archiv-Modul."""

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
from app.services.archive_service import (
    get_effective_permissions,
)
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


class TestPermissions:
    def test_effective_perms_own(
        self,
        db_session,
    ):
        _seed(db_session)
        d = _make_dir(
            db_session,
            "test",
            perms=["vbw_fu", "vbn_bi"],
        )
        effective = get_effective_permissions(db_session, d)
        assert "vbw_fu" in effective
        assert "vbn_bi" in effective

    def test_effective_perms_recursive(
        self,
        db_session,
    ):
        _seed(db_session)
        parent = _make_dir(
            db_session,
            "parent",
            perms=["vbw_fu"],
            recursive=True,
        )
        child = _make_dir(
            db_session,
            "child",
            parent_id=parent.id,
        )
        effective = get_effective_permissions(db_session, child)
        assert "vbw_fu" in effective

    def test_effective_perms_not_recursive(
        self,
        db_session,
    ):
        _seed(db_session)
        parent = _make_dir(
            db_session,
            "parent",
            perms=["vbw_fu"],
            recursive=False,
        )
        child = _make_dir(
            db_session,
            "child",
            parent_id=parent.id,
        )
        effective = get_effective_permissions(db_session, child)
        assert "vbw_fu" not in effective


class TestDirEndpoints:
    def test_root_requires_auth(
        self,
        client,
        db_session,
    ):
        resp = client.get("/api/archive/dirs")
        assert resp.status_code == 401

    def test_root_returns_dirs(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        _make_dir(
            db_session,
            "Public",
            perms=["vbw_fu"],
        )
        resp = client.get("/api/archive/dirs", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 0
        assert len(data["content"]["subdirs"]["insight"]) == 1

    def test_admin_sees_all(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        _make_dir(
            db_session,
            "Restricted",
            perms=["vbn_bi"],
        )
        resp = client.get("/api/archive/dirs", headers=headers)
        data = resp.json()
        total = len(data["content"]["subdirs"]["insight"]) + len(
            data["content"]["subdirs"]["admin"]
        )
        assert total >= 1

    def test_create_dir_requires_admin(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        resp = client.post(
            "/api/archive/dirs",
            json={
                "name": "NewDir",
                "permissions": [],
                "recursive_permissions": False,
            },
            headers=headers,
        )
        assert resp.status_code == 403

    def test_create_dir(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        resp = client.post(
            "/api/archive/dirs",
            json={
                "name": "NewDir",
                "permissions": ["vbw_fu"],
                "recursive_permissions": True,
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] > 0

    def test_delete_empty_dir_force(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "Empty")
        dir_id = d.id
        resp = client.delete(
            f"/api/archive/dirs/{dir_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        db_session.expire_all()
        assert db_session.get(ArchiveDir, dir_id) is None

    def test_delete_nonempty_soft(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "HasFiles")
        _make_file(db_session, dir_id=d.id)
        resp = client.delete(
            f"/api/archive/dirs/{d.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        db_session.expire_all()
        refreshed = db_session.get(ArchiveDir, d.id)
        assert refreshed is not None
        assert refreshed.deleted_at is not None

    def test_restore_dir(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(db_session, "Deleted")
        d.deleted_at = _now()
        db_session.commit()
        resp = client.patch(
            f"/api/archive/dirs/{d.id}/restore",
            headers=headers,
        )
        assert resp.status_code == 200
        db_session.refresh(d)
        assert d.deleted_at is None

    def test_move_dir(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        target = _make_dir(db_session, "Target")
        source = _make_dir(db_session, "Source")
        resp = client.post(
            f"/api/archive/dirs/{target.id}/receive",
            json={
                "type": "dir",
                "ids": [source.id],
                "action": "move",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        db_session.refresh(source)
        assert source.archive_dir_id == target.id

    def test_move_dir_circular_rejected(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        parent = _make_dir(db_session, "Parent")
        child = _make_dir(
            db_session,
            "Child",
            parent_id=parent.id,
        )
        resp = client.post(
            f"/api/archive/dirs/{child.id}/receive",
            json={
                "type": "dir",
                "ids": [parent.id],
                "action": "move",
            },
            headers=headers,
        )
        assert resp.status_code == 422


class TestFileEndpoints:
    def test_file_detail(
        self,
        client,
        db_session,
    ):
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

    def test_update_file(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        f = _make_file(db_session)
        resp = client.put(
            f"/api/archive/files/{f.id}",
            json={"description": "updated"},
            headers=headers,
        )
        assert resp.status_code == 200
        db_session.refresh(f)
        assert f.description == "updated"

    def test_delete_and_restore_file(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        f = _make_file(db_session)
        resp = client.delete(
            f"/api/archive/files/{f.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        db_session.refresh(f)
        assert f.deleted_at is not None

        resp = client.patch(
            f"/api/archive/files/{f.id}/restore",
            headers=headers,
        )
        assert resp.status_code == 200
        db_session.refresh(f)
        assert f.deleted_at is None


class TestComments:
    def test_create_comment(
        self,
        client,
        db_session,
    ):
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
        assert resp.json()["comment"]["content"] == "Tolle Datei!"

    def test_create_comment_too_short(
        self,
        client,
        db_session,
    ):
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

    def test_delete_comment_requires_admin(
        self,
        client,
        db_session,
    ):
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

    def test_admin_can_delete_comment(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, admin = _login_admin(db_session, client)
        d = _make_dir(db_session, "Dir")
        f = _make_file(db_session, dir_id=d.id)
        c = ArchiveFileComment(
            archive_file_id=f.id,
            content="Test comment here",
            created_by=admin.id,
            created_at=_now(),
        )
        db_session.add(c)
        db_session.commit()
        resp = client.delete(
            f"/api/archive/files/{f.id}/comments/{c.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        db_session.refresh(c)
        assert c.deleted_at is not None


class TestUpload:
    def test_upload_config(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        resp = client.get(
            "/api/archive/upload/config",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "extensions" in data
        assert "jpg" in data["extensions"]

    def test_unfiled_uploads(
        self,
        client,
        db_session,
    ):
        _seed(db_session)
        headers, _ = _login_user(db_session, client)
        resp = client.get(
            "/api/archive/upload/unfiled",
            headers=headers,
        )
        assert resp.status_code == 200
        assert "files" in resp.json()
