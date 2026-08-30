"""Tests für Standesdb API-Endpoints — Member/Contact CRUD, Search, Reference-Data."""

from datetime import date

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
            Badge(id=1, name="Fuxenband", group="jubelband", order=1),
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

    def test_search_combines_vorname_and_nachname(self, client, db_session):
        """Regression: a plain per-field OR (the pre-tsvector implementation)
        could never match "Maximilian Mustermann" - no single field alone
        contains the whole query string, only vorname+nachname together."""
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        db_session.add(
            Member(
                email="mm@test.at",
                vorname="Maximilian",
                nachname="Mustermann",
                org_id="vbw",
            )
        )
        db_session.commit()
        resp = client.get(
            "/api/standesdb/search",
            params={"q": "Maximilian Mustermann"},
            headers=headers,
        )
        assert resp.status_code == 200
        results = resp.json()["data"]
        assert any(
            r["type"] == "member" and "Mustermann" in r["label"] for r in results
        )

    def test_search_org_qualifier_disambiguates_same_name(self, client, db_session):
        """A name shared across both orgs can be disambiguated by adding the
        org id to the query - "schimpl vbn" must find only the vbn member,
        not the vbw member of the same surname."""
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        db_session.add_all(
            [
                Member(
                    email="michael@test.at",
                    vorname="Michael",
                    nachname="Schimpl",
                    org_id="vbw",
                ),
                Member(
                    email="ursula@test.at",
                    vorname="Ursula",
                    nachname="Schimpl",
                    org_id="vbn",
                ),
            ]
        )
        db_session.commit()
        resp = client.get(
            "/api/standesdb/search",
            params={"q": "schimpl vbn"},
            headers=headers,
        )
        assert resp.status_code == 200
        labels = [r["label"] for r in resp.json()["data"]]
        assert any("Ursula" in label for label in labels)
        assert not any("Michael" in label for label in labels)

    def test_search_fuzzy_fallback_finds_typo(self, client, db_session):
        """Stage 2 (pg_trgm): a typo'd name that Stage 1's exact/prefix
        match cannot reach is still found via typo-tolerant matching."""
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        db_session.add(
            Member(
                email="fuzzy@test.at",
                vorname="Alexander",
                nachname="Schimpl",
                org_id="vbw",
            )
        )
        db_session.commit()
        resp = client.get("/api/standesdb/search?q=Schimpel", headers=headers)
        assert resp.status_code == 200
        labels = [r["label"] for r in resp.json()["data"]]
        assert any("Schimpl" in label for label in labels)


# --- Member Detail ---


class TestMemberDetail:
    def test_get_member_active(self, client, db_session):
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

    def test_get_member_dismissed_gdpr(self, client, db_session):
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
    def test_create_member(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        resp = client.post(
            "/api/standesdb/members",
            json=_member_payload(vorname="Neu", nachname="Mitglied"),
            headers=headers,
        )
        assert resp.status_code == 201
        assert "id" in resp.json()

    def test_create_member_without_parent_stores_null_not_zero(
        self, client, db_session
    ):
        """The API contract uses 0 as the "no parent" sentinel
        (MemberSaveRequest.parent_id defaults to 0), but parent_id is a
        nullable self-referencing FK where 0 is never a valid member id.
        _normalize_member_input must convert 0 -> None before it's
        persisted, otherwise every member created without an explicit
        parent would violate the FK constraint as soon as it's enforced
        (e.g. by pg_restore recreating constraints from a dump)."""
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        resp = client.post(
            "/api/standesdb/members",
            json=_member_payload(vorname="Ohne", nachname="Elternteil"),
            headers=headers,
        )
        assert resp.status_code == 201

        member = db_session.get(Member, resp.json()["id"])
        assert member.parent_id is None

    def test_create_member_duplicate_rejected(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        client.post("/api/standesdb/members", json=_member_payload(), headers=headers)
        resp2 = client.post(
            "/api/standesdb/members", json=_member_payload(), headers=headers
        )
        assert resp2.status_code == 409

    def test_update_member(self, client, db_session):
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

    def test_update_member_not_found(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        resp = client.put(
            "/api/standesdb/members/99999",
            json=_member_payload(),
            headers=headers,
        )
        assert resp.status_code == 404

    def test_entlassen_locks_account(self, client, db_session):
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
    def test_get_contact(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        c = Contact(kontakttyp="person", name="KontaktTest")
        db_session.add(c)
        db_session.commit()
        resp = client.get(f"/api/standesdb/contacts/{c.id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "KontaktTest"

    def test_create_contact(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        resp = client.post(
            "/api/standesdb/contacts",
            json={"kontakttyp": "person", "name": "Neuer Kontakt"},
            headers=headers,
        )
        assert resp.status_code == 201

    def test_create_contact_duplicate_rejected(self, client, db_session):
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

    def test_update_contact(self, client, db_session):
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
    def test_search_parent(self, client, db_session):
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

    def test_search_parent_combines_vorname_and_nachname(self, client, db_session):
        """Regression: mirrors the same multi-field fix as the main search
        - "Vater Test" only matches via vorname+nachname combined."""
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        target = Member(
            email="child2@vbw.at", org_id="vbw", vorname="Kind", nachname="Test"
        )
        parent = Member(
            email="parent2@vbw.at", org_id="vbw", vorname="Vater", nachname="Test"
        )
        db_session.add_all([target, parent])
        db_session.commit()
        resp = client.get(
            f"/api/standesdb/members/{target.id}/searchparent",
            params={"q": "Vater Test"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert any(r["id"] == parent.id for r in resp.json()["data"])

    def test_search_parent_excludes_other_org(self, client, db_session):
        """Regression: org scoping must survive the tsvector/pg_trgm
        rewrite - a same-named member in the other org is never a valid
        Bürge candidate."""
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        target = Member(
            email="child3@vbw.at", org_id="vbw", vorname="Kind", nachname="Test"
        )
        other_org_namesake = Member(
            email="namesake@vbn.at", org_id="vbn", vorname="Vater", nachname="Test"
        )
        db_session.add_all([target, other_org_namesake])
        db_session.commit()
        resp = client.get(
            f"/api/standesdb/members/{target.id}/searchparent?q=Vater",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []
