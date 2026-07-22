"""Tests for system endpoints: health check, scheduled jobs, table browser."""

from datetime import date, datetime
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
            State(id="bi", label="Bandinhaber", order=1),
            Role(
                id="internetreferent",
                group="funktion",
                label="Internetreferent",
                order=1,
            ),
        ]
    )
    db.commit()


def _login_admin(db):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email="admin@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Admin",
        nachname="Test",
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
    return {"Authorization": f"Bearer {token}"}


def _login_unprivileged(db):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email="user@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Normal",
        nachname="User",
        org_id="vbw",
        state_id="bi",
    )
    db.add(m)
    db.commit()
    token, _, _ = create_user_session(db, m)
    return {"Authorization": f"Bearer {token}"}


def test_read_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "message": "Welcome to the vb-intern API!",
    }


def test_responses_are_never_cacheable(client):
    # Every response from this backend carries personal member data at some
    # point — browsers/proxies must never be allowed to cache it.
    response = client.get("/")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"


class TestScheduledJobs:
    def test_list_scheduled_jobs(self, client, db_session):
        _seed(db_session)
        headers = _login_admin(db_session)
        mock_jobs = [
            {
                "id": "cleanup",
                "name": "cleanup",
                "trigger": "interval[1:00:00]",
                "next_run": "01.07.2026, 08:00",
                "description": "Expired tokens cleanup",
            },
        ]
        with patch(
            "app.api.router_includes.system.get_scheduled_jobs",
            return_value=mock_jobs,
        ):
            resp = client.get("/api/system/scheduled-jobs", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "cleanup"
        assert data[0]["next_run"] == "01.07.2026, 08:00"


class TestTriggerBackup:
    def test_requires_systemAdmin(self, client, db_session):
        _seed(db_session)
        headers = _login_unprivileged(db_session)
        resp = client.post("/api/system/backups/trigger", headers=headers)
        assert resp.status_code == 403

    def test_trigger_backup_success(self, client, db_session, mock_s3):
        _seed(db_session)
        headers = _login_admin(db_session)
        with patch(
            "app.api.router_includes.system.run_backup",
            return_value="development-2026-07-15_12-00-00-manual.dump",
        ) as mock_run_backup:
            resp = client.post("/api/system/backups/trigger", headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["backup_name"] == "development-2026-07-15_12-00-00-manual.dump"
        assert datetime.fromisoformat(data["triggered_at"])
        mock_run_backup.assert_called_once_with(mock_s3, manual=True)

    def test_trigger_backup_failure_returns_500(self, client, db_session, mock_s3):
        _seed(db_session)
        headers = _login_admin(db_session)
        with patch(
            "app.api.router_includes.system.run_backup",
            side_effect=RuntimeError("pg_dump failed"),
        ):
            resp = client.post("/api/system/backups/trigger", headers=headers)
        assert resp.status_code == 500
        assert "pg_dump failed" in resp.json()["detail"]


class TestTableBrowser:
    def test_list_tables(self, client, db_session):
        _seed(db_session)
        headers = _login_admin(db_session)
        resp = client.get("/api/system/tables", headers=headers)
        assert resp.status_code == 200
        tables = resp.json()
        assert isinstance(tables, list)
        assert len(tables) > 0
        assert "members" in tables

    def test_get_table_data(self, client, db_session):
        _seed(db_session)
        headers = _login_admin(db_session)
        resp = client.get("/api/system/tables/orgs", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["table_name"] == "orgs"
        assert data["total"] == 1
        assert data["page"] == 1
        assert data["page_size"] == 25
        assert len(data["columns"]) > 0
        col_names = [c["name"] for c in data["columns"]]
        assert "id" in col_names
        assert len(data["rows"]) == 1

    def test_get_table_data_with_pagination(self, client, db_session):
        _seed(db_session)
        headers = _login_admin(db_session)
        resp = client.get(
            "/api/system/tables/orgs?page=2&page_size=10",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 2
        assert data["page_size"] == 10
        assert len(data["rows"]) == 0

    def test_get_table_data_invalid_table(self, client, db_session):
        _seed(db_session)
        headers = _login_admin(db_session)
        resp = client.get(
            "/api/system/tables/nonexistent_table",
            headers=headers,
        )
        assert resp.status_code == 404

    def test_column_metadata_includes_types(self, client, db_session):
        _seed(db_session)
        headers = _login_admin(db_session)
        resp = client.get("/api/system/tables/members", headers=headers)
        assert resp.status_code == 200
        columns = resp.json()["columns"]
        for col in columns:
            assert "name" in col
            assert "type" in col
            assert "nullable" in col
            assert "primary_key" in col

    def test_null_values_rendered_as_none(self, client, db_session):
        _seed(db_session)
        headers = _login_admin(db_session)
        resp = client.get("/api/system/tables/members", headers=headers)
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            for value in row.values():
                assert value is None or isinstance(value, str)
