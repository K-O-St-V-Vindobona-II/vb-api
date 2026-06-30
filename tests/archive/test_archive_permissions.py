"""Tests für Archiv-Berechtigungen (insight/admin, recursive, inheritance)."""

from datetime import UTC, date, datetime

import bcrypt

from app.models.archive_dir import ArchiveDir
from app.models.archive_file import ArchiveFile
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


class TestInsightPermissions:
    def test_insight_user_sees_permitted_dir(
        self,
        client,
        db_session,
    ):
        """User with org=vbw, state=fu sees dir
        that has permission vbw_fu."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client, org="vbw", state="fu")
        d = _make_dir(
            db_session,
            "Permitted",
            perms=["vbw_fu"],
        )
        resp = client.get(
            f"/api/archive/dirs/{d.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Permitted"

    def test_insight_user_cannot_see_unpermitted_dir(
        self,
        client,
        db_session,
    ):
        """User with vbw_fu does NOT see dir
        that only has vbn_bi permission."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client, org="vbw", state="fu")
        d = _make_dir(
            db_session,
            "Restricted",
            perms=["vbn_bi"],
        )
        resp = client.get(
            f"/api/archive/dirs/{d.id}",
            headers=headers,
        )
        assert resp.status_code == 403

    def test_admin_sees_all_dirs(
        self,
        client,
        db_session,
    ):
        """Admin can access dir even without
        matching org_state."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        d = _make_dir(
            db_session,
            "AnyPerms",
            perms=["vbn_fu"],
        )
        resp = client.get(
            f"/api/archive/dirs/{d.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "AnyPerms"


class TestRecursiveInheritance:
    def test_recursive_inheritance(
        self,
        client,
        db_session,
    ):
        """Parent has perms + recursive=true,
        child inherits access."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client, org="vbw", state="fu")
        parent = _make_dir(
            db_session,
            "Parent",
            perms=["vbw_fu"],
            recursive=True,
        )
        child = _make_dir(
            db_session,
            "Child",
            parent_id=parent.id,
        )
        resp = client.get(
            f"/api/archive/dirs/{child.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Child"

    def test_non_recursive_does_not_inherit(
        self,
        client,
        db_session,
    ):
        """Parent has perms + recursive=false,
        child does NOT inherit."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client, org="vbw", state="fu")
        parent = _make_dir(
            db_session,
            "Parent",
            perms=["vbw_fu"],
            recursive=False,
        )
        child = _make_dir(
            db_session,
            "Child",
            parent_id=parent.id,
        )
        resp = client.get(
            f"/api/archive/dirs/{child.id}",
            headers=headers,
        )
        assert resp.status_code == 403

    def test_multi_level_inheritance(
        self,
        client,
        db_session,
    ):
        """Grandchild inherits through
        recursive parent chain."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client, org="vbw", state="fu")
        grandparent = _make_dir(
            db_session,
            "Grandparent",
            perms=["vbw_fu"],
            recursive=True,
        )
        parent = _make_dir(
            db_session,
            "Parent",
            parent_id=grandparent.id,
            recursive=True,
        )
        child = _make_dir(
            db_session,
            "Child",
            parent_id=parent.id,
        )
        resp = client.get(
            f"/api/archive/dirs/{child.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Child"


class TestRootContentFiltering:
    def test_root_content_filters_by_permission(
        self,
        client,
        db_session,
    ):
        """GET /dirs returns only permitted dirs
        in insight category for normal user."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client, org="vbw", state="fu")
        _make_dir(
            db_session,
            "Visible",
            perms=["vbw_fu"],
        )
        _make_dir(
            db_session,
            "Hidden",
            perms=["vbn_bi"],
        )
        resp = client.get("/api/archive/dirs", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        insight_names = [d["name"] for d in data["content"]["subdirs"]["insight"]]
        admin_names = [d["name"] for d in data["content"]["subdirs"]["admin"]]
        assert "Visible" in insight_names
        assert "Hidden" not in insight_names
        # Normal user should not see admin category
        assert "Hidden" not in admin_names

    def test_root_content_admin_category(
        self,
        client,
        db_session,
    ):
        """Admin sees unpermitted dirs
        in admin category."""
        _seed(db_session)
        headers, _ = _login_admin(db_session, client)
        _make_dir(
            db_session,
            "NoMatch",
            perms=["vbn_fu"],
        )
        resp = client.get("/api/archive/dirs", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        # Admin org=vbw, state=bi; dir has vbn_fu
        # so it should NOT be in insight
        # but should appear in admin
        all_insight = [d["name"] for d in data["content"]["subdirs"]["insight"]]
        all_admin = [d["name"] for d in data["content"]["subdirs"]["admin"]]
        assert "NoMatch" not in all_insight
        assert "NoMatch" in all_admin


class TestFilePermissionInheritance:
    def test_file_inherits_dir_permission(
        self,
        client,
        db_session,
    ):
        """File in a permitted dir is accessible."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client, org="vbw", state="fu")
        d = _make_dir(
            db_session,
            "Allowed",
            perms=["vbw_fu"],
        )
        f = _make_file(db_session, dir_id=d.id, desc="photo")
        resp = client.get(
            f"/api/archive/files/{f.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "photo"

    def test_file_in_unpermitted_dir_blocked(
        self,
        client,
        db_session,
    ):
        """File in an unpermitted dir returns 403."""
        _seed(db_session)
        headers, _ = _login_user(db_session, client, org="vbw", state="fu")
        d = _make_dir(
            db_session,
            "Blocked",
            perms=["vbn_bi"],
        )
        f = _make_file(db_session, dir_id=d.id, desc="secret")
        resp = client.get(
            f"/api/archive/files/{f.id}",
            headers=headers,
        )
        assert resp.status_code == 403
