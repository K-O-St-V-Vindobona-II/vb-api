"""Router-level tests for the activity log endpoints (/tracking/activity)."""

import uuid
from datetime import UTC, date, datetime

import bcrypt

from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.request_log import RequestLog
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
    return {"Authorization": f"Bearer {token}"}, m


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


def _insert_log(
    db,
    member_id: uuid.UUID,
    created_at: datetime,
    method: str = "GET",
    path: str = "/api/test",
) -> RequestLog:
    log = RequestLog(
        client_ip="127.0.0.1",
        client_ips=["127.0.0.1"],
        member_id=member_id,
        request_method=method,
        request_path=path,
        response_status=200,
        memory_usage=0,
        created_at=created_at,
    )
    db.add(log)
    db.commit()
    return log


class TestListDaysWithActivity:
    def test_returns_days_for_month(self, client, db_session):
        _seed(db_session)
        headers, admin = _login_admin(db_session)
        _insert_log(db_session, admin.id, datetime(2026, 6, 15, 10, 0, tzinfo=UTC))

        resp = client.get("/api/tracking/activity?year=2026&month=6", headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["day"] == "2026-06-15"
        assert data[0]["members"][0]["id"] == str(admin.id)

    def test_requires_permission(self, client, db_session):
        _seed(db_session)
        headers = _login_unprivileged(db_session)
        resp = client.get("/api/tracking/activity?year=2026&month=6", headers=headers)
        assert resp.status_code == 403

    def test_empty_month_returns_empty_list(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        resp = client.get("/api/tracking/activity?year=2020&month=1", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetActivityForMemberDay:
    def test_returns_entries_for_that_day(self, client, db_session):
        _seed(db_session)
        headers, admin = _login_admin(db_session)
        _insert_log(
            db_session,
            admin.id,
            datetime(2026, 6, 15, 10, 0, tzinfo=UTC),
            path="/api/standesdb/members",
        )

        resp = client.get(
            f"/api/tracking/activity/members/{admin.id}?day=2026-06-15",
            headers=headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["member_name"] == "Admin Test"
        assert len(data["entries"]) == 1
        assert data["entries"][0]["request_path"] == "/api/standesdb/members"

    def test_404_for_unknown_member(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        resp = client.get(
            f"/api/tracking/activity/members/{uuid.uuid4()}?day=2026-06-15",
            headers=headers,
        )
        assert resp.status_code == 404


class TestGetActivityEntry:
    def test_returns_full_detail(self, client, db_session):
        _seed(db_session)
        headers, admin = _login_admin(db_session)
        log = _insert_log(
            db_session,
            admin.id,
            datetime.now(UTC),
            method="POST",
            path="/api/auth/login",
        )

        resp = client.get(f"/api/tracking/activity/{log.id}", headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["request_method"] == "POST"
        assert data["request_path"] == "/api/auth/login"
        assert data["member_name"] == "Admin Test"

    def test_404_for_missing(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        resp = client.get(f"/api/tracking/activity/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 404

    def test_requires_permission(self, client, db_session):
        _seed(db_session)
        headers = _login_unprivileged(db_session)
        resp = client.get(f"/api/tracking/activity/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 403
