"""Tests für die Änderungs-Benachrichtigungsmail nach Member/Contact-Save.

Geprüft werden:
- Member anlegen → Email mit change_type="store" wird getriggert
- Member bearbeiten mit Änderung → Email mit change_type="update"
- Member bearbeiten ohne Änderung → KEINE Email
- Empfänger: nur Members mit passender Permission + nicht-leerer Email
- Contact anlegen/bearbeiten → Email mit entry_type="contact"
"""

from datetime import date

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
            member_id=m.id_uuid,
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
                member_id=m.id_uuid,
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
    def test_create_member_sends_email(self, client, db_session, mock_arq_pool):
        _setup(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)

        resp = client.post(
            "/api/standesdb/members",
            json=_member_payload(),
            headers=headers,
        )
        assert resp.status_code == 201

        mock_arq_pool.enqueue_job.assert_called_once()
        args = mock_arq_pool.enqueue_job.call_args
        assert args[0][0] == "task_send_entry_changed_email"
        assert args[0][2] == "member"
        assert args.kwargs["change_type"] == "store"

    def test_create_member_email_has_correct_cn(
        self, client, db_session, mock_arq_pool
    ):
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

        args = mock_arq_pool.enqueue_job.call_args
        entry_cn = args[0][3]
        assert "Franz" in entry_cn or "Testikus" in entry_cn


# ─── Member Update → Email ─────────────────────────────


class TestMemberUpdateNotification:
    def test_update_member_with_change_sends_email(
        self, client, db_session, mock_arq_pool
    ):
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

        mock_arq_pool.enqueue_job.assert_called_once()
        args = mock_arq_pool.enqueue_job.call_args
        assert args[0][0] == "task_send_entry_changed_email"
        assert args[0][2] == "member"
        assert args.kwargs["change_type"] == "update"

    def test_update_member_without_change_no_email(
        self, client, db_session, mock_arq_pool
    ):
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
        mock_arq_pool.enqueue_job.assert_not_called()

    def test_update_email_contains_diff(self, client, db_session, mock_arq_pool):
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

        args = mock_arq_pool.enqueue_job.call_args
        diff = args[0][4]
        assert "vorname" in diff
        assert diff["vorname"]["old"] == "Alt"
        assert diff["vorname"]["new"] == "Neu"


# ─── Contact Notifications ─────────────────────────────


class TestContactNotification:
    def test_create_contact_sends_email(self, client, db_session, mock_arq_pool):
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
        assert resp.status_code == 201

        mock_arq_pool.enqueue_job.assert_called_once()
        args = mock_arq_pool.enqueue_job.call_args
        assert args[0][0] == "task_send_entry_changed_email"
        assert args[0][2] == "contact"
        assert args.kwargs["change_type"] == "store"

    def test_update_contact_sends_email(self, client, db_session, mock_arq_pool):
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
        mock_arq_pool.enqueue_job.reset_mock()

        resp = client.put(
            f"/api/standesdb/contacts/{contact_id}",
            json={
                "kontakttyp": "person",
                "name": "Neuer Name",
            },
            headers=headers,
        )
        assert resp.status_code == 200

        mock_arq_pool.enqueue_job.assert_called_once()
        args = mock_arq_pool.enqueue_job.call_args
        assert args[0][0] == "task_send_entry_changed_email"
        assert args[0][2] == "contact"
        assert args.kwargs["change_type"] == "update"
