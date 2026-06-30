"""Tests für die Änderungs-Benachrichtigungsmail nach Member/Contact-Save.

Geprüft werden:
- Member anlegen → Email mit change_type="store" wird getriggert
- Member bearbeiten mit Änderung → Email mit change_type="update"
- Member bearbeiten ohne Änderung → KEINE Email
- Empfänger: nur Members mit passender Permission + nicht-leerer Email
- Contact anlegen/bearbeiten → Email mit entry_type="contact"
"""

from datetime import date
from unittest.mock import patch

import bcrypt

from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import create_user_session
from app.services.permission_service import (
    get_emails_with_permission,
)


def _setup(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Org(id="vbn", label="VBN", order=2),
            State(id="fu", label="Fux", order=1),
            Role(id="standesfuehrer", group="chc", label="Standesführer", order=1),
            Role(id="senior", group="chc", label="Senior", order=2),
        ]
    )
    db.commit()


def _admin(db, org_id="vbw", email="admin@vbw.at"):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email=email,
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


# ─── Empfänger-Ermittlung ──────────────────────────────


class TestGetEmailsWithPermission:
    def test_returns_admin_emails(self, db_session):
        _setup(db_session)
        _admin(db_session, email="sf@vbw.at")
        result = get_emails_with_permission(db_session, "standesdbVbwAdmin")
        assert "sf@vbw.at" in result

    def test_excludes_members_without_permission(self, db_session):
        _setup(db_session)
        _admin(db_session, email="sf@vbw.at")
        normal = Member(
            email="normal@vbw.at",
            org_id="vbw",
            vorname="Normal",
            nachname="User",
        )
        db_session.add(normal)
        db_session.commit()
        result = get_emails_with_permission(db_session, "standesdbVbwAdmin")
        assert "sf@vbw.at" in result
        assert "normal@vbw.at" not in result

    def test_excludes_empty_emails(self, db_session):
        _setup(db_session)
        m = Member(
            email=None,
            org_id="vbw",
            vorname="NoEmail",
            nachname="User",
        )
        db_session.add(m)
        db_session.commit()
        db_session.add(
            MemberRole(
                member_id=m.id,
                role_id="standesfuehrer",
                startdate=date(2000, 1, 1),
                enddate=None,
            )
        )
        db_session.commit()
        result = get_emails_with_permission(db_session, "standesdbVbwAdmin")
        assert len([e for e in result if e is None]) == 0

    def test_vbn_admin_not_in_vbw_results(self, db_session):
        _setup(db_session)
        _admin(db_session, org_id="vbn", email="sf@vbn.at")
        result = get_emails_with_permission(db_session, "standesdbVbwAdmin")
        assert "sf@vbn.at" not in result


# ─── Member Create → Email ─────────────────────────────


class TestMemberCreateNotification:
    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_create_member_sends_email(self, mock_send, client, db_session):
        _setup(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)

        resp = client.post(
            "/api/standesdb/members",
            json=_member_payload(),
            headers=headers,
        )
        assert resp.status_code == 200

        mock_send.assert_called_once()
        args = mock_send.call_args
        assert args[0][1] == "member"
        assert args[0][4] == "store"

    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_create_member_email_has_correct_cn(self, mock_send, client, db_session):
        _setup(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)

        client.post(
            "/api/standesdb/members",
            json=_member_payload(
                vorname="Franz",
                nachname="Test",
                couleurname="Testikus",
            ),
            headers=headers,
        )

        args = mock_send.call_args
        entry_cn = args[0][2]
        assert "Franz" in entry_cn or "Testikus" in entry_cn


# ─── Member Update → Email ─────────────────────────────


class TestMemberUpdateNotification:
    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_update_member_with_change_sends_email(self, mock_send, client, db_session):
        _setup(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)

        target = Member(
            email="target@vbw.at",
            org_id="vbw",
            vorname="Alt",
            nachname="Name",
        )
        db_session.add(target)
        db_session.commit()

        resp = client.put(
            f"/api/standesdb/members/{target.id}",
            json=_member_payload(vorname="Neu", nachname="Name"),
            headers=headers,
        )
        assert resp.status_code == 200

        mock_send.assert_called_once()
        args = mock_send.call_args
        assert args[0][1] == "member"
        assert args[0][4] == "update"

    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_update_member_without_change_no_email(self, mock_send, client, db_session):
        _setup(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)

        target = Member(
            email="target@vbw.at",
            org_id="vbw",
            vorname="Max",
            nachname="Muster",
            gruender=False,
            entlassen=False,
            verstorben=False,
            zustellungen="deaktiviert",
            chroniclemail=False,
            auth_locked=True,
        )
        db_session.add(target)
        db_session.commit()

        resp = client.put(
            f"/api/standesdb/members/{target.id}",
            json=_member_payload(email="target@vbw.at"),
            headers=headers,
        )
        assert resp.status_code == 200
        mock_send.assert_not_called()

    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_update_email_contains_diff(self, mock_send, client, db_session):
        _setup(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)

        target = Member(
            email="target@vbw.at",
            org_id="vbw",
            vorname="Alt",
            nachname="Name",
        )
        db_session.add(target)
        db_session.commit()

        client.put(
            f"/api/standesdb/members/{target.id}",
            json=_member_payload(vorname="Neu", nachname="Name"),
            headers=headers,
        )

        args = mock_send.call_args
        diff = args[0][3]
        assert "vorname" in diff
        assert diff["vorname"]["old"] == "Alt"
        assert diff["vorname"]["new"] == "Neu"


# ─── Contact Notifications ─────────────────────────────


class TestContactNotification:
    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_create_contact_sends_email(self, mock_send, client, db_session):
        _setup(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)

        resp = client.post(
            "/api/standesdb/contacts",
            json={
                "kontakttyp": "person",
                "name": "Testperson",
            },
            headers=headers,
        )
        assert resp.status_code == 200

        mock_send.assert_called_once()
        args = mock_send.call_args
        assert args[0][1] == "contact"
        assert args[0][4] == "store"

    @patch("app.api.router_includes.standesdb.send_entry_changed_email")
    def test_update_contact_sends_email(self, mock_send, client, db_session):
        _setup(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)

        resp_create = client.post(
            "/api/standesdb/contacts",
            json={
                "kontakttyp": "person",
                "name": "Testperson",
            },
            headers=headers,
        )
        contact_id = resp_create.json()["id"]
        mock_send.reset_mock()

        resp = client.put(
            f"/api/standesdb/contacts/{contact_id}",
            json={
                "kontakttyp": "person",
                "name": "Neuer Name",
            },
            headers=headers,
        )
        assert resp.status_code == 200

        mock_send.assert_called_once()
        args = mock_send.call_args
        assert args[0][1] == "contact"
        assert args[0][4] == "update"
