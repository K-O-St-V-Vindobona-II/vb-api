"""Tests für GET /api/standesdb/roles — Chargen-Liste."""

from datetime import UTC, date, datetime
from unittest.mock import patch

import bcrypt

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
        ]
    )
    db.commit()
    db.add_all(
        [
            Role(id="x", group="chc", label="Senior", order=0),
            Role(id="xx", group="chc", label="Consenior", order=1),
            Role(id="phil-x", group="philchc", label="Philistersenior", order=9),
            Role(id="archivar", group="funktion", label="Archivar", order=16),
            Role(
                id="vg_vors", group="verbindungsgericht", label="VG-Vorsitz", order=46
            ),
        ]
    )
    db.commit()


def _login(db, _client, org_id="vbw"):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email=f"user@{org_id}.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Test",
        nachname="User",
        org_id=org_id,
    )
    db.add(m)
    db.commit()
    db.add(
        MemberRole(
            member_id=m.id,
            role_id="x",
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    token, _, _ = create_user_session(db, m)
    return {"Authorization": f"Bearer {token}"}, m


def _add_role_assignment(db, member_id, role_id, start, end=None):
    db.add(
        MemberRole(
            member_id=member_id,
            role_id=role_id,
            startdate=start,
            enddate=end,
        )
    )
    db.commit()


class TestRolesListEndpoint:
    def test_requires_auth(self, client, db_session):
        resp = client.get("/api/standesdb/roles")
        assert resp.status_code == 401

    def test_returns_all_roles_sorted(self, client, db_session):
        _seed(db_session)
        headers, _ = _login(db_session, client)
        resp = client.get("/api/standesdb/roles", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "roles" in data
        assert "semester" in data
        assert "year" in data
        labels = [r["label"] for r in data["roles"]]
        assert labels == [
            "Senior",
            "Consenior",
            "Philistersenior",
            "Archivar",
            "VG-Vorsitz",
        ]

    def test_current_assignments_shown(self, client, db_session):
        _seed(db_session)
        headers, user = _login(db_session, client)

        hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
        vbn_member = Member(
            email="vbn@vbn.at",
            auth_password=hashed,
            auth_locked=True,
            vorname="Anna",
            nachname="Muster",
            org_id="vbn",
        )
        db_session.add(vbn_member)
        db_session.commit()

        _add_role_assignment(
            db_session, user.id, "xx", date(2025, 8, 1), date(2026, 1, 31)
        )
        _add_role_assignment(
            db_session, vbn_member.id, "xx", date(2025, 8, 1), date(2026, 1, 31)
        )

        with patch("app.services.standesdb_service.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 10, 15, tzinfo=UTC)
            resp = client.get("/api/standesdb/roles", headers=headers)

        assert resp.status_code == 200
        roles = resp.json()["roles"]
        consenior = next(r for r in roles if r["label"] == "Consenior")
        assert consenior["vbw"] is not None
        assert consenior["vbw"]["cn"] == "Test User"
        assert consenior["vbn"] is not None
        assert consenior["vbn"]["cn"] == "Anna Muster"

    def test_expired_assignment_not_shown(self, client, db_session):
        _seed(db_session)
        headers, user = _login(db_session, client)
        _add_role_assignment(
            db_session, user.id, "xx", date(2024, 2, 1), date(2024, 7, 31)
        )

        with patch("app.services.standesdb_service.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 10, 15, tzinfo=UTC)
            resp = client.get("/api/standesdb/roles", headers=headers)

        roles = resp.json()["roles"]
        consenior = next(r for r in roles if r["label"] == "Consenior")
        assert consenior["vbw"] is None
        assert consenior["vbn"] is None

    def test_semester_filter_ss(self, client, db_session):
        _seed(db_session)
        headers, user = _login(db_session, client)
        _add_role_assignment(
            db_session, user.id, "xx", date(2025, 2, 1), date(2025, 7, 31)
        )

        resp = client.get(
            "/api/standesdb/roles?year=2025&semester=ss",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["semester"] == "ss"
        assert data["year"] == 2025
        consenior = next(r for r in data["roles"] if r["label"] == "Consenior")
        assert consenior["vbw"] is not None

    def test_semester_filter_ws(self, client, db_session):
        _seed(db_session)
        headers, user = _login(db_session, client)
        _add_role_assignment(
            db_session, user.id, "xx", date(2025, 8, 1), date(2026, 1, 31)
        )

        resp = client.get(
            "/api/standesdb/roles?year=2025&semester=ws",
            headers=headers,
        )
        assert resp.status_code == 200
        consenior = next(r for r in resp.json()["roles"] if r["label"] == "Consenior")
        assert consenior["vbw"] is not None

    def test_semester_filter_excludes_other(self, client, db_session):
        _seed(db_session)
        headers, user = _login(db_session, client)
        _add_role_assignment(
            db_session, user.id, "xx", date(2025, 2, 1), date(2025, 7, 31)
        )

        resp = client.get(
            "/api/standesdb/roles?year=2025&semester=ws",
            headers=headers,
        )
        consenior = next(r for r in resp.json()["roles"] if r["label"] == "Consenior")
        assert consenior["vbw"] is None

    def test_open_ended_assignment(self, client, db_session):
        _seed(db_session)
        headers, user = _login(db_session, client)
        _add_role_assignment(db_session, user.id, "archivar", date(2020, 1, 1), None)

        resp = client.get(
            "/api/standesdb/roles?year=2025&semester=ss",
            headers=headers,
        )
        archivar = next(r for r in resp.json()["roles"] if r["label"] == "Archivar")
        assert archivar["vbw"] is not None
        assert archivar["vbw"]["enddate"] is None

    def test_invalid_semester_rejected(self, client, db_session):
        _seed(db_session)
        headers, _ = _login(db_session, client)
        resp = client.get(
            "/api/standesdb/roles?year=2025&semester=xx",
            headers=headers,
        )
        assert resp.status_code == 422

    def test_year_without_semester_rejected(self, client, db_session):
        _seed(db_session)
        headers, _ = _login(db_session, client)
        resp = client.get(
            "/api/standesdb/roles?year=2025",
            headers=headers,
        )
        assert resp.status_code == 422

    def test_semester_without_year_rejected(self, client, db_session):
        _seed(db_session)
        headers, _ = _login(db_session, client)
        resp = client.get(
            "/api/standesdb/roles?semester=ss",
            headers=headers,
        )
        assert resp.status_code == 422

    def test_auto_semester_detection_ss(self, client, db_session):
        _seed(db_session)
        headers, _ = _login(db_session, client)

        with patch("app.services.standesdb_service.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 5, 1, tzinfo=UTC)
            resp = client.get("/api/standesdb/roles", headers=headers)

        data = resp.json()
        assert data["semester"] == "ss"
        assert data["year"] == 2025

    def test_auto_semester_detection_ws(self, client, db_session):
        _seed(db_session)
        headers, _ = _login(db_session, client)

        with patch("app.services.standesdb_service.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 11, 1, tzinfo=UTC)
            resp = client.get("/api/standesdb/roles", headers=headers)

        data = resp.json()
        assert data["semester"] == "ws"
        assert data["year"] == 2025

    def test_all_groups_present(self, client, db_session):
        _seed(db_session)
        headers, _ = _login(db_session, client)
        resp = client.get("/api/standesdb/roles", headers=headers)
        groups = {r["group"] for r in resp.json()["roles"]}
        assert groups == {"chc", "philchc", "funktion", "verbindungsgericht"}

    def test_first_match_per_org_returned(self, client, db_session):
        """Nur die erste Zuweisung (nach startdate) pro Org wird zurückgegeben."""
        _seed(db_session)
        headers, user = _login(db_session, client)

        hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
        other = Member(
            email="other@vbw.at",
            auth_password=hashed,
            auth_locked=True,
            vorname="Zweiter",
            nachname="Senior",
            org_id="vbw",
        )
        db_session.add(other)
        db_session.commit()

        _add_role_assignment(
            db_session, user.id, "phil-x", date(2025, 2, 1), date(2025, 7, 31)
        )
        _add_role_assignment(
            db_session, other.id, "phil-x", date(2025, 4, 1), date(2025, 7, 31)
        )

        resp = client.get(
            "/api/standesdb/roles?year=2025&semester=ss",
            headers=headers,
        )
        philx = next(r for r in resp.json()["roles"] if r["label"] == "Philistersenior")
        assert philx["vbw"]["cn"] == "Test User"
