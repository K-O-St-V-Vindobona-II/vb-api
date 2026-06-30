from datetime import date

import bcrypt

from app.models.badge import Badge
from app.models.contact import Contact
from app.models.contacts_log import ContactsLog
from app.models.key import Key
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.members_log import MembersLog
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import create_user_session


def _setup_reference_data(db):
    """Seed orgs, states, roles for tests."""
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Org(id="vbn", label="VBN", order=2),
            State(id="fu", label="Fux", order=1),
            State(id="bu", label="Bursch", order=2),
            State(id="up", label="Philister", order=3),
            Role(
                id="standesfuehrer",
                group="chc",
                label="Standesführer",
                order=1,
            ),
            Role(
                id="internetreferent",
                group="it",
                label="Internetreferent",
                order=2,
            ),
            Badge(
                id=1,
                name="Fuxenband",
                group="band",
                order=1,
            ),
            Key(id=1, name="Haustorschlüssel"),
        ]
    )
    db.commit()


def _create_admin(db, org_id="vbw"):
    """Create an admin user with standesfuehrer role."""
    hashed = bcrypt.hashpw(b"admin", bcrypt.gensalt()).decode("utf-8")
    admin = Member(
        email=f"admin@{org_id}.at",
        auth_password=hashed,
        vorname="Admin",
        nachname="Test",
        org_id=org_id,
        auth_locked=False,
    )
    db.add(admin)
    db.commit()

    mr = MemberRole(
        member_id=admin.id,
        role_id="standesfuehrer",
        startdate=date(2000, 1, 1),
        enddate=None,
    )
    db.add(mr)
    db.commit()
    return admin


def _auth_headers(_client, db, admin):
    """Get auth headers for an admin user."""
    token, _, _ = create_user_session(db, admin)
    return {"Authorization": f"Bearer {token}"}


# --- Stats ---


def test_stats_endpoint(client, db_session):
    """GET /stats returns member and contact counts."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    db_session.add(
        Member(
            email="m1@vbw.at",
            org_id="vbw",
            entlassen=False,
            verstorben=False,
        )
    )
    db_session.add(
        Member(
            email="m2@vbn.at",
            org_id="vbn",
            entlassen=True,
            verstorben=False,
        )
    )
    db_session.commit()

    resp = client.get("/api/standesdb/stats", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "member" in data
    assert "contact" in data


# --- Search ---


def test_search_requires_min_3_chars(client, db_session):
    """Search requires at least 3 characters."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    resp = client.get(
        "/api/standesdb/search?q=ab",
        headers=headers,
    )
    assert resp.status_code == 422


def test_search_finds_members_and_contacts(client, db_session):
    """Search returns both members and contacts."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    db_session.add(
        Member(
            email="max@test.at",
            vorname="Max",
            nachname="Muster",
            org_id="vbw",
        )
    )
    db_session.add(
        Contact(
            kontakttyp="person",
            name="Max Kontakt",
        )
    )
    db_session.commit()

    resp = client.get(
        "/api/standesdb/search?q=Max",
        headers=headers,
    )
    assert resp.status_code == 200
    results = resp.json()["data"]
    types = {r["type"] for r in results}
    assert "member" in types
    assert "contact" in types


# --- Member Detail ---


def test_get_member_active(client, db_session):
    """Active member returns full detail."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    member = Member(
        email="detail@test.at",
        vorname="Detail",
        nachname="Test",
        couleurname="Detl",
        org_id="vbw",
        state_id="bu",
        entlassen=False,
    )
    db_session.add(member)
    db_session.commit()

    resp = client.get(
        f"/api/standesdb/members/{member.id}",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["cn"] == "Detail Test v/o Detl"
    assert data["org_id"] == "vbw"
    assert "roles_history" in data
    assert "tree" in data


def test_get_member_dismissed_gdpr(client, db_session):
    """Dismissed member returns GDPR-minimal data."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    member = Member(
        email="gdpr@test.at",
        vorname="Geheim",
        nachname="Daten",
        couleurname="Geh",
        org_id="vbw",
        entlassen=True,
    )
    db_session.add(member)
    db_session.commit()

    resp = client.get(
        f"/api/standesdb/members/{member.id}",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["dataprotection"] == "dismissed"
    assert "vorname" not in data
    assert "email" not in data


def test_get_member_not_found(client, db_session):
    """Non-existent member returns 404."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    resp = client.get(
        "/api/standesdb/members/99999",
        headers=headers,
    )
    assert resp.status_code == 404


# --- Member Create ---


def test_create_member_success(client, db_session):
    """Admin can create a member in their org."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session, "vbw")
    headers = _auth_headers(client, db_session, admin)

    resp = client.post(
        "/api/standesdb/members",
        headers=headers,
        json={
            "vorname": "Neu",
            "nachname": "Mitglied",
            "org_id": "vbw",
            "state_id": "fu",
            "zustellungen": "deaktiviert",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_member_wrong_org(client, db_session):
    """VBW admin cannot create VBN member."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session, "vbw")
    headers = _auth_headers(client, db_session, admin)

    resp = client.post(
        "/api/standesdb/members",
        headers=headers,
        json={
            "vorname": "Falsch",
            "nachname": "Org",
            "org_id": "vbn",
            "zustellungen": "deaktiviert",
        },
    )
    assert resp.status_code == 403


def test_create_member_duplicate_name(client, db_session):
    """Duplicate name combination is rejected."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    db_session.add(
        Member(
            email="existing@test.at",
            vorname="Doppelt",
            nachname="Gemoppelt",
            org_id="vbw",
        )
    )
    db_session.commit()

    resp = client.post(
        "/api/standesdb/members",
        headers=headers,
        json={
            "vorname": "Doppelt",
            "nachname": "Gemoppelt",
            "org_id": "vbw",
            "zustellungen": "deaktiviert",
        },
    )
    assert resp.status_code == 409


# --- Member Update ---


def test_update_member_entlassen_locks(client, db_session):
    """Setting entlassen=true auto-locks account."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    member = Member(
        email="lock@test.at",
        vorname="Lock",
        nachname="Test",
        org_id="vbw",
        auth_locked=False,
        zustellungen="adresse_privat",
    )
    db_session.add(member)
    db_session.commit()

    resp = client.put(
        f"/api/standesdb/members/{member.id}",
        headers=headers,
        json={
            "vorname": "Lock",
            "nachname": "Test",
            "org_id": "vbw",
            "entlassen": True,
            "zustellungen": "adresse_privat",
        },
    )
    assert resp.status_code == 200

    db_session.refresh(member)
    assert member.auth_locked is True
    assert member.zustellungen == "deaktiviert"
    assert member.chroniclemail is False


# --- Contact ---


def test_get_contact(client, db_session):
    """GET contact returns detail."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    contact = Contact(
        kontakttyp="person",
        name="Kontaktperson",
    )
    db_session.add(contact)
    db_session.commit()

    resp = client.get(
        f"/api/standesdb/contacts/{contact.id}",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Kontaktperson"


def test_create_contact_requires_permission(client, db_session):
    """Non-admin cannot create contacts."""
    _setup_reference_data(db_session)

    hashed = bcrypt.hashpw(b"user", bcrypt.gensalt()).decode("utf-8")
    user = Member(
        email="user@vbw.at",
        auth_password=hashed,
        vorname="Normal",
        nachname="User",
        org_id="vbw",
        auth_locked=False,
    )
    db_session.add(user)
    db_session.commit()

    token, _, _ = create_user_session(db_session, user)
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(
        "/api/standesdb/contacts",
        headers=headers,
        json={
            "kontakttyp": "person",
            "name": "Neuer Kontakt",
        },
    )
    assert resp.status_code == 403


def test_create_contact_success(client, db_session):
    """Admin can create contacts."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    resp = client.post(
        "/api/standesdb/contacts",
        headers=headers,
        json={
            "kontakttyp": "organisation",
            "name": "Neue Firma",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_delete_contact_success(client, db_session):
    """Admin can soft-delete a contact."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    contact = Contact(
        kontakttyp="person",
        name="Löschbar",
    )
    db_session.add(contact)
    db_session.commit()

    resp = client.delete(
        f"/api/standesdb/contacts/{contact.id}",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    db_session.refresh(contact)
    assert contact.deleted_at is not None


def test_delete_contact_hides_from_detail(client, db_session):
    """Deleted contact returns 404 on GET."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    contact = Contact(
        kontakttyp="person",
        name="Versteckt",
    )
    db_session.add(contact)
    db_session.commit()

    client.delete(
        f"/api/standesdb/contacts/{contact.id}",
        headers=headers,
    )

    resp = client.get(
        f"/api/standesdb/contacts/{contact.id}",
        headers=headers,
    )
    assert resp.status_code == 404


def test_delete_contact_hides_from_search(client, db_session):
    """Deleted contact does not appear in search."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    contact = Contact(
        kontakttyp="person",
        name="Suchtest",
    )
    db_session.add(contact)
    db_session.commit()

    client.delete(
        f"/api/standesdb/contacts/{contact.id}",
        headers=headers,
    )

    resp = client.get(
        "/api/standesdb/search?q=Suchtest",
        headers=headers,
    )
    results = resp.json()["data"]
    assert not any(r["id"] == contact.id for r in results)


def test_delete_contact_hides_from_stats(client, db_session):
    """Deleted contact not counted in stats."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    def _contact_sum(resp):
        c = resp.json()["contact"]
        return c["common"] + c["vbw"] + c["vbn"]

    resp1 = client.get(
        "/api/standesdb/stats",
        headers=headers,
    )
    before = _contact_sum(resp1)

    contact = Contact(
        kontakttyp="person",
        name="Zähltest",
    )
    db_session.add(contact)
    db_session.commit()

    resp2 = client.get(
        "/api/standesdb/stats",
        headers=headers,
    )
    assert _contact_sum(resp2) == before + 1

    client.delete(
        f"/api/standesdb/contacts/{contact.id}",
        headers=headers,
    )

    resp3 = client.get(
        "/api/standesdb/stats",
        headers=headers,
    )
    assert _contact_sum(resp3) == before


def test_delete_contact_requires_permission(client, db_session):
    """Non-admin cannot delete contacts."""
    _setup_reference_data(db_session)

    hashed = bcrypt.hashpw(b"user", bcrypt.gensalt()).decode("utf-8")
    user = Member(
        email="user2@vbw.at",
        auth_password=hashed,
        vorname="Normal",
        nachname="User",
        org_id="vbw",
        auth_locked=False,
    )
    db_session.add(user)
    db_session.commit()
    token, _, _ = create_user_session(db_session, user)
    headers = {"Authorization": f"Bearer {token}"}

    contact = Contact(
        kontakttyp="person",
        name="Geschützt",
    )
    db_session.add(contact)
    db_session.commit()

    resp = client.delete(
        f"/api/standesdb/contacts/{contact.id}",
        headers=headers,
    )
    assert resp.status_code == 403


def test_delete_contact_404_for_missing(client, db_session):
    """Delete non-existent contact returns 404."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    resp = client.delete(
        "/api/standesdb/contacts/99999",
        headers=headers,
    )
    assert resp.status_code == 404


def test_delete_contact_idempotent(client, db_session):
    """Double-delete returns 404 on second call."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    contact = Contact(
        kontakttyp="person",
        name="Doppelt",
    )
    db_session.add(contact)
    db_session.commit()

    resp1 = client.delete(
        f"/api/standesdb/contacts/{contact.id}",
        headers=headers,
    )
    assert resp1.status_code == 200

    resp2 = client.delete(
        f"/api/standesdb/contacts/{contact.id}",
        headers=headers,
    )
    assert resp2.status_code == 404


# --- Reference Data ---


def test_reference_data(client, db_session):
    """Reference data endpoint returns all sets."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    resp = client.get(
        "/api/standesdb/reference-data",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["orgs"]) == 2
    assert len(data["states"]) == 3
    assert len(data["roles"]) >= 2
    assert len(data["badges"]) >= 1
    assert len(data["keys"]) >= 1


# --- Phone Validation ---


def test_invalid_phone_rejected(client, db_session):
    """Invalid phone number format is rejected."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    resp = client.post(
        "/api/standesdb/members",
        headers=headers,
        json={
            "vorname": "Phone",
            "nachname": "Test",
            "org_id": "vbw",
            "rufnummer_mobil": "abc123",
            "zustellungen": "deaktiviert",
        },
    )
    assert resp.status_code == 422


# --- Change Logs ---


def test_create_member_writes_log(client, db_session):
    """Creating a member writes log entries."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    resp = client.post(
        "/api/standesdb/members",
        headers=headers,
        json={
            "vorname": "Log",
            "nachname": "Test",
            "email": "log@vbw.at",
            "org_id": "vbw",
            "state_id": "fu",
            "zustellungen": "deaktiviert",
        },
    )
    assert resp.status_code == 200
    member_id = resp.json()["id"]

    logs = (
        db_session.query(MembersLog)
        .filter(
            MembersLog.member_id == member_id,
        )
        .all()
    )
    assert len(logs) > 0
    assert all(l.action == "store" for l in logs)
    assert all(l.modified_by == admin.id for l in logs)
    keys = {l.key for l in logs}
    assert "vorname" in keys
    assert "nachname" in keys


def test_update_member_writes_log(client, db_session):
    """Updating a member writes log entries for changed fields only."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    client.post(
        "/api/standesdb/members",
        headers=headers,
        json={
            "vorname": "Before",
            "nachname": "Update",
            "email": "upd@vbw.at",
            "org_id": "vbw",
            "state_id": "fu",
            "zustellungen": "deaktiviert",
        },
    )
    member_id = db_session.query(Member).filter(Member.email == "upd@vbw.at").first().id

    db_session.query(MembersLog).filter(MembersLog.member_id == member_id).delete()
    db_session.commit()

    client.put(
        f"/api/standesdb/members/{member_id}",
        headers=headers,
        json={
            "vorname": "After",
            "nachname": "Update",
            "email": "upd@vbw.at",
            "org_id": "vbw",
            "state_id": "fu",
            "zustellungen": "deaktiviert",
        },
    )

    logs = (
        db_session.query(MembersLog)
        .filter(
            MembersLog.member_id == member_id,
        )
        .all()
    )
    assert len(logs) == 1
    assert logs[0].action == "update"
    assert logs[0].key == "vorname"
    assert logs[0].old == "Before"
    assert logs[0].new == "After"


def test_create_contact_writes_log(client, db_session):
    """Creating a contact writes log entries."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    resp = client.post(
        "/api/standesdb/contacts",
        headers=headers,
        json={"kontakttyp": "person", "name": "Log Kontakt"},
    )
    assert resp.status_code == 200
    contact_id = resp.json()["id"]

    logs = (
        db_session.query(ContactsLog)
        .filter(
            ContactsLog.contact_id == contact_id,
        )
        .all()
    )
    assert len(logs) > 0
    assert all(l.action == "store" for l in logs)
    keys = {l.key for l in logs}
    assert "kontakttyp" in keys
    assert "name" in keys


def test_update_contact_writes_log(client, db_session):
    """Updating a contact writes log entries for changed fields."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    contact = Contact(
        kontakttyp="person",
        name="Vor Update",
    )
    db_session.add(contact)
    db_session.commit()

    client.put(
        f"/api/standesdb/contacts/{contact.id}",
        headers=headers,
        json={"kontakttyp": "person", "name": "Nach Update"},
    )

    logs = (
        db_session.query(ContactsLog)
        .filter(
            ContactsLog.contact_id == contact.id,
        )
        .all()
    )
    assert len(logs) == 1
    assert logs[0].action == "update"
    assert logs[0].key == "name"
    assert logs[0].old == "Vor Update"
    assert logs[0].new == "Nach Update"


def test_delete_contact_writes_log(client, db_session):
    """Deleting a contact writes a log entry."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    contact = Contact(
        kontakttyp="person",
        name="Löschlog",
    )
    db_session.add(contact)
    db_session.commit()

    client.delete(
        f"/api/standesdb/contacts/{contact.id}",
        headers=headers,
    )

    logs = (
        db_session.query(ContactsLog)
        .filter(
            ContactsLog.contact_id == contact.id,
        )
        .all()
    )
    assert len(logs) == 1
    assert logs[0].action == "delete"
    assert logs[0].key == "deleted_at"
    assert logs[0].modified_by == admin.id


def test_no_log_on_unchanged_save(client, db_session):
    """Saving without changes creates no log entries."""
    _setup_reference_data(db_session)
    admin = _create_admin(db_session)
    headers = _auth_headers(client, db_session, admin)

    contact = Contact(
        kontakttyp="person",
        name="Unverändert",
    )
    db_session.add(contact)
    db_session.commit()

    client.put(
        f"/api/standesdb/contacts/{contact.id}",
        headers=headers,
        json={"kontakttyp": "person", "name": "Unverändert"},
    )

    logs = (
        db_session.query(ContactsLog)
        .filter(
            ContactsLog.contact_id == contact.id,
        )
        .all()
    )
    assert len(logs) == 0
