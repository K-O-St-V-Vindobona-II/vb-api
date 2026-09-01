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
            member_id=m.id_uuid,
            role_id="standesfuehrer",
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.add(
        MemberRole(
            member_id=m.id_uuid,
            role_id="internetreferent",
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    return m


def _system_admin_only(db, org="vbw"):
    """A member with systemAdmin but deliberately WITHOUT any standesdb
    admin role - used to prove the changelog endpoints no longer accept
    systemAdmin as a substitute for standesdb-specific admin permissions."""
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email=f"systemadmin@{org}.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="System",
        nachname="Admin",
        org_id=org,
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
                modified_by=admin.id_uuid,
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
        data = resp.json()["items"]
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
                modified_by=admin.id_uuid,
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
        data = resp.json()["items"]
        assert len(data) >= 1
        assert data[0]["modified_by_name"] == "Admin User"

    def test_member_changelog_pagination(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        target = Member(
            email="paginated@vbw.at",
            vorname="Paginated",
            nachname="Member",
            org_id="vbw",
            state_id="fu",
        )
        db_session.add(target)
        db_session.commit()
        for i in range(3):
            db_session.add(
                MembersLog(
                    member_id=target.id,
                    modified_by=admin.id_uuid,
                    modified_at=datetime.now(UTC),
                    action="update",
                    key=f"field{i}",
                    old="old",
                    new="new",
                )
            )
        db_session.commit()

        page1 = client.get(
            f"/api/standesdb/members/{target.id}/changelog?page=1&page_size=2",
            headers=h,
        ).json()
        page2 = client.get(
            f"/api/standesdb/members/{target.id}/changelog?page=2&page_size=2",
            headers=h,
        ).json()

        assert page1["total"] == 3
        assert len(page1["items"]) == 2
        assert page2["total"] == 3
        assert len(page2["items"]) == 1

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
        assert resp.json() == {"items": [], "total": 0, "page": 1, "page_size": 25}

    def test_member_changelog_404_for_unknown_member(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        resp = client.get("/api/standesdb/members/99999/changelog", headers=h)
        assert resp.status_code == 404

    def test_member_changelog_rejects_system_admin_without_standesdb_role(
        self, client, db_session
    ):
        """systemAdmin alone must no longer grant changelog access - only
        the org-matching standesdb admin permission does."""
        _seed(db_session)
        system_admin = _system_admin_only(db_session)
        h = _headers(db_session, system_admin)
        target = Member(
            email="target2@vbw.at",
            vorname="Target",
            nachname="Two",
            org_id="vbw",
            state_id="fu",
        )
        db_session.add(target)
        db_session.commit()

        resp = client.get(
            f"/api/standesdb/members/{target.id}/changelog",
            headers=h,
        )
        assert resp.status_code == 403

    def test_member_changelog_rejects_admin_of_other_org(self, client, db_session):
        """A standesdbVbwAdmin must not see the changelog of a VBN member."""
        _seed(db_session)
        vbw_admin = _admin(db_session, org="vbw")
        h = _headers(db_session, vbw_admin)
        vbn_member = Member(
            email="vbnmember@vbn.at",
            vorname="Vbn",
            nachname="Member",
            org_id="vbn",
            state_id="fu",
        )
        db_session.add(vbn_member)
        db_session.commit()

        resp = client.get(
            f"/api/standesdb/members/{vbn_member.id}/changelog",
            headers=h,
        )
        assert resp.status_code == 403

    def test_contact_changelog_rejects_system_admin_without_standesdb_role(
        self, client, db_session
    ):
        """systemAdmin alone must no longer grant contact-changelog access."""
        _seed(db_session)
        system_admin = _system_admin_only(db_session)
        h = _headers(db_session, system_admin)
        contact = _make_contact(db_session, name="Guarded Contact")

        resp = client.get(
            f"/api/standesdb/contacts/{contact.id}/changelog",
            headers=h,
        )
        assert resp.status_code == 403

    def test_member_changelog_excludes_technical_fields(self, client, db_session):
        """Technical/audit bookkeeping keys (e.g. auth_lastsignal, bumped on
        every request via a raw bulk UPDATE that bypasses the diff
        mechanism) must never show up in the Änderungshistorie, even if
        legacy rows for them already exist in the DB."""
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        target = Member(
            email="technical@vbw.at",
            vorname="Technical",
            nachname="Member",
            org_id="vbw",
            state_id="fu",
        )
        db_session.add(target)
        db_session.commit()
        for key in ("auth_lastsignal", "updated_at", "vorname"):
            db_session.add(
                MembersLog(
                    member_id=target.id,
                    modified_by=admin.id_uuid,
                    modified_at=datetime.now(UTC),
                    action="update",
                    key=key,
                    old="old",
                    new="new",
                )
            )
        db_session.commit()

        resp = client.get(
            f"/api/standesdb/members/{target.id}/changelog",
            headers=h,
        )
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["key"] == "vorname"

    def test_contact_changelog_excludes_technical_fields(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        contact = _make_contact(db_session, name="Technical Contact")
        for key in ("modified_at", "email"):
            db_session.add(
                ContactsLog(
                    contact_id=contact.id,
                    modified_by=admin.id_uuid,
                    modified_at=datetime.now(UTC),
                    action="update",
                    key=key,
                    old="old",
                    new="new",
                )
            )
        db_session.commit()

        resp = client.get(
            f"/api/standesdb/contacts/{contact.id}/changelog",
            headers=h,
        )
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["key"] == "email"

    def test_contact_changelog_keeps_delete_entries(self, client, db_session):
        """deleted_at is the sole record of a contact's deletion and must
        stay visible, unlike other bookkeeping timestamp columns."""
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        contact = _make_contact(db_session, name="Deleted Contact")
        db_session.add(
            ContactsLog(
                contact_id=contact.id,
                modified_by=admin.id_uuid,
                modified_at=datetime.now(UTC),
                action="delete",
                key="deleted_at",
                old=None,
                new=str(datetime.now(UTC)),
            )
        )
        db_session.commit()

        resp = client.get(
            f"/api/standesdb/contacts/{contact.id}/changelog",
            headers=h,
        )
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["key"] == "deleted_at"
        assert data["items"][0]["action"] == "delete"


class TestMemberAuthActivity:
    def test_auth_activity_for_matching_standesdb_org_admin(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        h = _headers(db_session, admin)
        target = Member(
            email="authtarget@vbw.at",
            vorname="Auth",
            nachname="Target",
            org_id="vbw",
            state_id="fu",
            auth_lastlogin=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
            auth_lastsignal=datetime(2026, 8, 2, 11, 0, tzinfo=UTC),
            auth_lastlogout=datetime(2026, 8, 2, 11, 5, tzinfo=UTC),
        )
        db_session.add(target)
        db_session.commit()

        resp = client.get(
            f"/api/standesdb/members/{target.id}/auth-activity",
            headers=h,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["auth_lastlogin"] is not None
        assert data["auth_lastsignal"] is not None
        assert data["auth_lastlogout"] is not None

    def test_auth_activity_rejects_system_admin_without_standesdb_role(
        self, client, db_session
    ):
        """systemAdmin alone must no longer grant access - only the
        org-matching standesdb admin permission does (same rule as the
        changelog endpoints)."""
        _seed(db_session)
        system_admin = _system_admin_only(db_session)
        h = _headers(db_session, system_admin)
        target = Member(
            email="authtarget2@vbw.at",
            vorname="Auth",
            nachname="Target2",
            org_id="vbw",
            state_id="fu",
        )
        db_session.add(target)
        db_session.commit()

        resp = client.get(
            f"/api/standesdb/members/{target.id}/auth-activity",
            headers=h,
        )
        assert resp.status_code == 403

    def test_auth_activity_rejects_admin_of_other_org(self, client, db_session):
        """A standesdbVbwAdmin must not see the auth activity of a VBN member."""
        _seed(db_session)
        vbw_admin = _admin(db_session, org="vbw")
        h = _headers(db_session, vbw_admin)
        vbn_member = Member(
            email="authvbnmember@vbn.at",
            vorname="Vbn",
            nachname="Member",
            org_id="vbn",
            state_id="fu",
        )
        db_session.add(vbn_member)
        db_session.commit()

        resp = client.get(
            f"/api/standesdb/members/{vbn_member.id}/auth-activity",
            headers=h,
        )
        assert resp.status_code == 403
