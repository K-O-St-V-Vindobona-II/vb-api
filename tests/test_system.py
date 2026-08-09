"""Tests for system endpoints: health check, scheduled jobs, table browser."""

from datetime import UTC, date, datetime
from unittest.mock import patch

import bcrypt

from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.scheduled_task_run import ScheduledTaskRun
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


class TestEnvironment:
    def test_requires_authentication(self, client):
        resp = client.get("/api/system/environment")
        assert resp.status_code == 401

    def test_returns_environment_for_any_authenticated_member(self, client, db_session):
        """No systemAdmin required — every member sees their own profile's
        stage footer, so any authenticated member must be able to read
        this."""
        _seed(db_session)
        headers = _login_unprivileged(db_session)
        resp = client.get("/api/system/environment", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"environment": "test"}


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
        assert data[0]["last_run"] is None

    def test_list_scheduled_jobs_includes_last_run(self, client, db_session):
        _seed(db_session)
        headers = _login_admin(db_session)
        started = datetime(2026, 8, 4, 3, 0, 0, tzinfo=UTC)
        db_session.add(
            ScheduledTaskRun(
                job_id="cleanup",
                started_at=started,
                finished_at=started,
                exit_code=0,
                output="3 removed",
            )
        )
        db_session.commit()
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
        last_run = resp.json()[0]["last_run"]
        assert last_run["exit_code"] == 0
        assert last_run["output"] == "3 removed"
        # Regression guard: Pydantic v2 serializes Decimal as a JSON string
        # by default — duration_seconds must come through as a number.
        assert isinstance(last_run["duration_seconds"], int | float)

    def test_requires_systemAdmin(self, client, db_session):
        _seed(db_session)
        headers = _login_unprivileged(db_session)
        resp = client.get("/api/system/scheduled-jobs", headers=headers)
        assert resp.status_code == 403


class TestScheduledJobHistory:
    def test_requires_systemAdmin(self, client, db_session):
        _seed(db_session)
        headers = _login_unprivileged(db_session)
        resp = client.get("/api/system/scheduled-jobs/cleanup/history", headers=headers)
        assert resp.status_code == 403

    def test_returns_paginated_runs_newest_first(self, client, db_session):
        _seed(db_session)
        headers = _login_admin(db_session)
        older = datetime(2026, 8, 1, 3, 0, 0, tzinfo=UTC)
        newer = datetime(2026, 8, 4, 3, 0, 0, tzinfo=UTC)
        db_session.add_all(
            [
                ScheduledTaskRun(
                    job_id="cleanup",
                    started_at=older,
                    finished_at=older,
                    exit_code=0,
                ),
                ScheduledTaskRun(
                    job_id="cleanup",
                    started_at=newer,
                    finished_at=newer,
                    exit_code=1,
                    output="boom",
                ),
                ScheduledTaskRun(
                    job_id="db_backup",
                    started_at=newer,
                    finished_at=newer,
                    exit_code=0,
                ),
            ]
        )
        db_session.commit()

        resp = client.get("/api/system/scheduled-jobs/cleanup/history", headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["page"] == 1
        assert data["page_size"] == 25
        assert [item["exit_code"] for item in data["items"]] == [1, 0]
        assert data["items"][0]["output"] == "boom"

    def test_pagination_params(self, client, db_session):
        _seed(db_session)
        headers = _login_admin(db_session)
        base = datetime(2026, 8, 4, 3, 0, 0, tzinfo=UTC)
        db_session.add_all(
            [
                ScheduledTaskRun(
                    job_id="cleanup",
                    started_at=base,
                    finished_at=base,
                    exit_code=0,
                )
                for _ in range(3)
            ]
        )
        db_session.commit()

        resp = client.get(
            "/api/system/scheduled-jobs/cleanup/history?page=2&page_size=2",
            headers=headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 2
        assert data["page_size"] == 2
        assert len(data["items"]) == 1


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


class TestTriggerDownsync:
    def test_requires_systemAdmin(self, client, db_session):
        _seed(db_session)
        headers = _login_unprivileged(db_session)
        resp = client.post("/api/system/downsync/trigger", headers=headers)
        assert resp.status_code == 403

    def test_starts_as_background_task_on_non_production(self, client, db_session):
        # The test suite's own app_environment is "test" (see
        # TestEnvironment above) - already non-production, no patching
        # needed to exercise the success path.
        _seed(db_session)
        headers = _login_admin(db_session)
        with patch("app.api.router_includes.system.job_downsync") as mock_job_downsync:
            resp = client.post("/api/system/downsync/trigger", headers=headers)
        assert resp.status_code == 202
        assert resp.json() == {"status": "started"}
        mock_job_downsync.assert_called_once_with()

    def test_rejected_in_production(self, client, db_session):
        _seed(db_session)
        headers = _login_admin(db_session)
        with (
            patch(
                "app.services.system_service.get_app_environment",
                return_value="production",
            ),
            patch("app.api.router_includes.system.job_downsync") as mock_job_downsync,
        ):
            resp = client.post("/api/system/downsync/trigger", headers=headers)
        assert resp.status_code == 409
        assert "Production" in resp.json()["detail"]
        mock_job_downsync.assert_not_called()


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
