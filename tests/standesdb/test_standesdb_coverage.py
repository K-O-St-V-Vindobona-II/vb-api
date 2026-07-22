"""Coverage tests for standesdb router and service."""

import io
from datetime import UTC, date, datetime

import bcrypt

from app.models.contact import Contact
from app.models.contacts_log import ContactsLog
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.members_log import MembersLog
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import create_user_session


def _seed(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Org(id="vbn", label="VBN", order=2),
            State(id="fu", label="Fux", order=1),
            State(id="bi", label="Bandinhaber", order=3),
            Role(
                id="standesfuehrer",
                group="chc",
                label="Standesführer",
                order=1,
            ),
            Role(
                id="internetreferent",
                group="funktion",
                label="Internetreferent",
                order=2,
            ),
        ]
    )
    db.commit()


def _admin(db, org="vbw"):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email=f"admin@{org}.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Admin",
        nachname="User",
        org_id=org,
        state_id="bi",
    )
    db.add(m)
    db.commit()
    db.add(
        MemberRole(
            member_id=m.id,
            role_id="standesfuehrer",
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.add(
        MemberRole(
            member_id=m.id,
            role_id="internetreferent",
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    return m


def _headers(db, member):
    token, _, _ = create_user_session(db, member)
    return {"Authorization": f"Bearer {token}"}


def _make_contact(db, name="Test Kontakt", org_id="vbw"):
    c = Contact(
        kontakttyp="organisation",
        name=name,
        org_id=org_id,
    )
    db.add(c)
    db.commit()
    return c


class TestMemberNotFound:
    def test_auth_activity_404(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        resp = client.get("/api/standesdb/members/99999/auth-activity", headers=h)
        assert resp.status_code == 404

    def test_list_images_404(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        resp = client.get("/api/standesdb/members/99999/images", headers=h)
        assert resp.status_code == 404

    def test_upload_image_404(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        buf = io.BytesIO(b"fake")
        resp = client.post(
            "/api/standesdb/members/99999/images",
            headers=h,
            files={"file": ("test.jpg", buf, "image/jpeg")},
        )
        assert resp.status_code == 404

    def test_update_image_404(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        resp = client.put(
            "/api/standesdb/members/99999/images/1",
            json={"description": "x"},
            headers=h,
        )
        assert resp.status_code == 404

    def test_delete_image_404(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        resp = client.delete(
            "/api/standesdb/members/99999/images/1",
            headers=h,
        )
        assert resp.status_code == 404

    def test_search_parent_404(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        resp = client.get(
            "/api/standesdb/members/99999/searchparent?q=test",
            headers=h,
        )
        assert resp.status_code == 404


class TestMemberNoOrg:
    def test_update_member_no_org(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        orphan = Member(
            email="orphan@test.at",
            vorname="Orphan",
            nachname="User",
            org_id=None,
            state_id="fu",
        )
        db_session.add(orphan)
        db_session.commit()
        resp = client.put(
            f"/api/standesdb/members/{orphan.id}",
            json={"vorname": "New", "nachname": "Name", "org_id": "vbw"},
            headers=h,
        )
        assert resp.status_code == 422
        assert "Verbindung" in resp.json()["detail"]

    def test_search_parent_no_org(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        orphan = Member(
            email="orphan2@test.at",
            vorname="Orphan",
            nachname="Two",
            org_id=None,
        )
        db_session.add(orphan)
        db_session.commit()
        resp = client.get(
            f"/api/standesdb/members/{orphan.id}/searchparent?q=test",
            headers=h,
        )
        assert resp.status_code == 422
        assert "Verbindung" in resp.json()["detail"]


class TestContactImages:
    def test_list_contact_images_not_found(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        resp = client.get("/api/standesdb/contacts/99999/images", headers=h)
        assert resp.status_code == 404

    def test_list_contact_images_soft_deleted(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        c = _make_contact(db_session)
        c.deleted_at = datetime.now(UTC)
        db_session.commit()
        resp = client.get(f"/api/standesdb/contacts/{c.id}/images", headers=h)
        assert resp.status_code == 404

    def test_upload_contact_image_not_found(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        buf = io.BytesIO(b"fake")
        resp = client.post(
            "/api/standesdb/contacts/99999/images",
            headers=h,
            files={"file": ("test.jpg", buf, "image/jpeg")},
        )
        assert resp.status_code == 404


class TestChangelog:
    def test_member_changelog_with_modifier(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        target = Member(
            email="target@vbw.at",
            vorname="Target",
            nachname="Member",
            org_id="vbw",
            state_id="fu",
        )
        db_session.add(target)
        db_session.commit()
        db_session.add(
            MembersLog(
                member_id=target.id,
                modified_by=admin.id,
                modified_at=datetime.now(UTC),
                action="update",
                key="nachname",
                old="Alt",
                new="Neu",
            )
        )
        db_session.commit()
        resp = client.get(
            f"/api/standesdb/members/{target.id}/changelog",
            headers=h,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["modified_by_name"] == "Admin User"
        assert data[0]["key"] == "nachname"

    def test_contact_changelog_with_modifier(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        contact = _make_contact(db_session, name="Log Contact")
        db_session.add(
            ContactsLog(
                contact_id=contact.id,
                modified_by=admin.id,
                modified_at=datetime.now(UTC),
                action="update",
                key="email",
                old="old@test.at",
                new="new@test.at",
            )
        )
        db_session.commit()
        resp = client.get(
            f"/api/standesdb/contacts/{contact.id}/changelog",
            headers=h,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["modified_by_name"] == "Admin User"

    def test_member_changelog_empty(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        target = Member(
            email="empty@vbw.at",
            vorname="Empty",
            nachname="Log",
            org_id="vbw",
            state_id="fu",
        )
        db_session.add(target)
        db_session.commit()
        resp = client.get(
            f"/api/standesdb/members/{target.id}/changelog",
            headers=h,
        )
        assert resp.status_code == 200
        assert resp.json() == []
