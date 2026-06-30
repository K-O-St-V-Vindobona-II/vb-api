"""Tests für Standesdb API-Endpoints — Member/Contact CRUD, Search, Reference-Data."""

from datetime import date
from unittest.mock import patch

import bcrypt

from app.models.badge import Badge
from app.models.contact import Contact
from app.models.key import Key
from app.models.member import Member
from app.models.member_role import MemberRole
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
            State(id="bu", label="Bursch", order=2),
            Role(id="standesfuehrer", group="chc", label="Standesführer", order=1),
            Role(id="senior", group="chc", label="Senior", order=2),
            Badge(id=1, name="Fuxenband", group="band", order=1),
            Key(id=1, name="Haustorschlüssel"),
        ]
    )
    db.commit()


def _admin(db, org_id="vbw"):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email=f"admin@{org_id}.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Admin",
        nachname="User",
        org_id=org_id,
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
    db.commit()
    return m


def _headers(_client, db, admin):
    token, _, _ = create_user_session(db, admin)
    return {"Authorization": f"Bearer {token}"}


def _member_payload(**overrides):
    base = {
        "vorname": "Max",
        "nachname": "Muster",
        "org_id": "vbw",
        "gruender": False,
        "entlassen": False,
        "verstorben": False,
        "zustellungen": "deaktiviert",
        "chroniclemail": False,
        "auth_locked": True,
        "geburtsdatum_accuracy": 0,
        "aufnahmedatum_accuracy": 0,
        "branderdatum_accuracy": 0,
        "burschungsdatum_accuracy": 0,
        "philistrierungsdatum_accuracy": 0,
        "entlassungsdatum_accuracy": 0,
        "sterbedatum_accuracy": 0,
    }
    base.update(overrides)
    return base


# --- Reference Data ---


class TestReferenceData:
    def test_returns_all_categories(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        resp = client.get("/api/standesdb/reference-data", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "orgs" in data
        assert "states" in data
        assert "roles" in data
        assert "badges" in data
        assert "keys" in data
        assert len(data["orgs"]) == 2
        assert len(data["roles"]) == 2


# --- Search ---


class TestSearch:
    def test_search_finds_member(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        db_session.add(
            Member(
                email="max@test.at",
                vorname="Max",
                nachname="Muster",
                couleurname="Testikus",
                org_id="vbw",
            )
        )
        db_session.commit()
        resp = client.get("/api/standesdb/search?q=Testikus", headers=headers)
        assert resp.status_code == 200
        results = resp.json()["data"]
        assert any(r["label"] and "Testikus" in r["label"] for r in results)

    def test_search_finds_contact(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        db_session.add(
            Contact(
                kontakttyp="person",
                name="Suchkontakt",
            )
        )
        db_session.commit()
        resp = client.get("/api/standesdb/search?q=Suchkontakt", headers=headers)
        assert resp.status_code == 200
        results = resp.json()["data"]
        assert any(r["type"] == "contact" for r in results)

    def test_search_min_3_chars(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        resp = client.get("/api/standesdb/search?q=ab", headers=headers)
        assert resp.status_code == 422


# --- Member Detail ---


class TestMemberDetail:
    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_get_member_active(self, mock_mail, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        db_session.add(
            Member(
                email="show@vbw.at",
                vorname="Show",
                nachname="Test",
                couleurname="Showikus",
                org_id="vbw",
            )
        )
        db_session.commit()
        target = db_session.query(Member).filter_by(email="show@vbw.at").first()
        resp = client.get(f"/api/standesdb/members/{target.id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["vorname"] == "Show"
        assert data["cn"] is not None
        assert "org_label" in data

    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_get_member_dismissed_gdpr(self, mock_mail, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        db_session.add(
            Member(
                email="dismissed@vbw.at",
                vorname="Gone",
                nachname="User",
                org_id="vbw",
                entlassen=True,
            )
        )
        db_session.commit()
        target = db_session.query(Member).filter_by(email="dismissed@vbw.at").first()
        resp = client.get(f"/api/standesdb/members/{target.id}", headers=headers)
        assert resp.status_code == 200
        assert "dataprotection" in resp.json()

    def test_get_member_not_found(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        resp = client.get("/api/standesdb/members/99999", headers=headers)
        assert resp.status_code == 404


# --- Member Create/Update ---


class TestMemberCRUD:
    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_create_member(self, mock_mail, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        resp = client.post(
            "/api/standesdb/members",
            json=_member_payload(vorname="Neu", nachname="Mitglied"),
            headers=headers,
        )
        assert resp.status_code == 200
        assert "id" in resp.json()

    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_create_member_duplicate_rejected(self, mock_mail, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        client.post("/api/standesdb/members", json=_member_payload(), headers=headers)
        resp2 = client.post(
            "/api/standesdb/members", json=_member_payload(), headers=headers
        )
        assert resp2.status_code == 409

    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_update_member(self, mock_mail, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        target = Member(
            email="upd@vbw.at",
            org_id="vbw",
            vorname="Alt",
            nachname="Name",
        )
        db_session.add(target)
        db_session.commit()
        resp = client.put(
            f"/api/standesdb/members/{target.id}",
            json=_member_payload(vorname="Neu", nachname="Name", email="upd@vbw.at"),
            headers=headers,
        )
        assert resp.status_code == 200

    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_update_member_not_found(self, mock_mail, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        resp = client.put(
            "/api/standesdb/members/99999",
            json=_member_payload(),
            headers=headers,
        )
        assert resp.status_code == 404

    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_entlassen_locks_account(self, mock_mail, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        target = Member(
            email="lock@vbw.at",
            org_id="vbw",
            vorname="Lock",
            nachname="Test",
            auth_locked=False,
        )
        db_session.add(target)
        db_session.commit()
        client.put(
            f"/api/standesdb/members/{target.id}",
            json=_member_payload(
                vorname="Lock",
                nachname="Test",
                email="lock@vbw.at",
                entlassen=True,
            ),
            headers=headers,
        )
        db_session.expire_all()
        assert target.auth_locked is True


# --- Contact CRUD ---


class TestContactCRUD:
    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_get_contact(self, mock_mail, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        c = Contact(kontakttyp="person", name="KontaktTest")
        db_session.add(c)
        db_session.commit()
        resp = client.get(f"/api/standesdb/contacts/{c.id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "KontaktTest"

    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_create_contact(self, mock_mail, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        resp = client.post(
            "/api/standesdb/contacts",
            json={"kontakttyp": "person", "name": "Neuer Kontakt"},
            headers=headers,
        )
        assert resp.status_code == 200

    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_create_contact_duplicate_rejected(self, mock_mail, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        client.post(
            "/api/standesdb/contacts",
            json={"kontakttyp": "person", "name": "Duplikat"},
            headers=headers,
        )
        resp2 = client.post(
            "/api/standesdb/contacts",
            json={"kontakttyp": "person", "name": "Duplikat"},
            headers=headers,
        )
        assert resp2.status_code == 409

    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_update_contact(self, mock_mail, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        c = Contact(kontakttyp="person", name="Alter Name")
        db_session.add(c)
        db_session.commit()
        resp = client.put(
            f"/api/standesdb/contacts/{c.id}",
            json={"kontakttyp": "person", "name": "Neuer Name"},
            headers=headers,
        )
        assert resp.status_code == 200

    def test_get_contact_not_found(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        resp = client.get("/api/standesdb/contacts/99999", headers=headers)
        assert resp.status_code == 404


# --- Search Parent ---


class TestSearchParent:
    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_search_parent(self, mock_mail, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        target = Member(
            email="child@vbw.at", org_id="vbw", vorname="Kind", nachname="Test"
        )
        parent = Member(
            email="parent@vbw.at", org_id="vbw", vorname="Vater", nachname="Test"
        )
        db_session.add_all([target, parent])
        db_session.commit()
        resp = client.get(
            f"/api/standesdb/members/{target.id}/searchparent?q=Vater",
            headers=headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) >= 1
