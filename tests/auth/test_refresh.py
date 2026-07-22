from datetime import UTC, datetime, timedelta

import bcrypt
import pytest
from fastapi.testclient import TestClient

from app.core.security import (
    build_refresh_cookie_value,
    generate_refresh_secret,
    hash_refresh_secret,
)
from app.models.member import Member
from app.models.personal_access_token import PersonalAccessToken

PASSWORD = "testpass123"
HASHED = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    from app.core.rate_limit import limiter

    limiter.reset()
    return


@pytest.fixture
def member(db_session):
    m = Member(
        email="refresh@test.at",
        auth_password=HASHED,
        auth_locked=False,
        nachname="Test",
        vorname="Refresh",
    )
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    return m


def _login(client: TestClient) -> str:
    client.cookies.jar.clear()
    resp = client.post(
        "/api/auth/login",
        data={"username": "refresh@test.at", "password": PASSWORD},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _get_refresh_cookie(client: TestClient) -> str:
    for cookie in client.cookies.jar:
        if cookie.name == "refresh_token":
            return cookie.value
    return ""


def _set_refresh_cookie(client: TestClient, value: str) -> None:
    to_remove = [c for c in client.cookies.jar if c.name == "refresh_token"]
    for c in to_remove:
        client.cookies.jar.clear(c.domain, c.path, c.name)
    client.cookies.set("refresh_token", value)


class TestLoginSetsCookie:
    def test_login_returns_refresh_cookie(self, client, member):
        _login(client)
        cookie = _get_refresh_cookie(client)
        assert cookie is not None
        assert ":" in cookie

    def test_login_cookie_attributes(self, client, member):
        resp = client.post(
            "/api/auth/login",
            data={"username": "refresh@test.at", "password": PASSWORD},
        )
        cookie_header = resp.headers.get("set-cookie", "")
        assert "httponly" in cookie_header.lower()
        assert "samesite=lax" in cookie_header.lower()
        assert "path=/api/auth" in cookie_header.lower()


class TestRefreshSuccess:
    def test_refresh_returns_new_access_token(self, client, member):
        _login(client)
        resp = client.post("/api/auth/refresh")
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_refreshed_token_works_for_api(self, client, member):
        _login(client)
        resp = client.post("/api/auth/refresh")
        new_token = resp.json()["access_token"]
        me_resp = client.get(
            "/api/members/me",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert me_resp.status_code == 200

    def test_refresh_rotates_cookie(self, client, member):
        _login(client)
        cookie_before = _get_refresh_cookie(client)
        client.post("/api/auth/refresh")
        cookie_after = _get_refresh_cookie(client)
        assert cookie_before != cookie_after

    def test_multiple_refreshes_work(self, client, member):
        _login(client)
        for _ in range(5):
            resp = client.post("/api/auth/refresh")
            assert resp.status_code == 200


class TestRefreshReuseDetection:
    def test_old_cookie_after_rotation_fails(self, client, member, db_session):
        _login(client)
        old_cookie = _get_refresh_cookie(client)
        client.post("/api/auth/refresh")

        _set_refresh_cookie(client, old_cookie)
        resp = client.post("/api/auth/refresh")
        assert resp.status_code == 401

    def test_reuse_destroys_session(self, client, member, db_session):
        _login(client)
        old_cookie = _get_refresh_cookie(client)
        client.post("/api/auth/refresh")

        _set_refresh_cookie(client, old_cookie)
        client.post("/api/auth/refresh")
        sessions = (
            db_session.query(PersonalAccessToken)
            .filter(PersonalAccessToken.member_id == member.id)
            .count()
        )
        assert sessions == 0


class TestRefreshFailures:
    def test_no_cookie_returns_401(self, client, member):
        _set_refresh_cookie(client, "")
        resp = client.post("/api/auth/refresh")
        assert resp.status_code == 401

    def test_malformed_cookie_returns_401(self, client, member):
        _set_refresh_cookie(client, "garbage-no-colon")
        resp = client.post("/api/auth/refresh")
        assert resp.status_code == 401

    def test_expired_session_returns_401(self, client, member, db_session):
        secret = generate_refresh_secret()
        pat = PersonalAccessToken(
            member_id=member.id,
            name="session",
            token="expired-session",
            refresh_token_hash=hash_refresh_secret(secret),
            last_used_at=datetime.now(UTC),
            created_at=datetime.now(UTC) - timedelta(days=8),
            updated_at=datetime.now(UTC),
        )
        db_session.add(pat)
        db_session.commit()
        cookie = build_refresh_cookie_value("expired-session", secret)
        _set_refresh_cookie(client, cookie)
        resp = client.post("/api/auth/refresh")
        assert resp.status_code == 401

    def test_idle_timeout_returns_401(self, client, member, db_session):
        secret = generate_refresh_secret()
        pat = PersonalAccessToken(
            member_id=member.id,
            name="session",
            token="idle-session",
            refresh_token_hash=hash_refresh_secret(secret),
            last_used_at=datetime(2020, 1, 1, tzinfo=UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(pat)
        db_session.commit()
        cookie = build_refresh_cookie_value("idle-session", secret)
        _set_refresh_cookie(client, cookie)
        resp = client.post("/api/auth/refresh")
        assert resp.status_code == 401

    def test_locked_user_returns_401(self, client, member, db_session):
        _login(client)
        member.auth_locked = True
        db_session.commit()
        resp = client.post("/api/auth/refresh")
        assert resp.status_code == 401

    def test_nonexistent_session_returns_401(self, client, member):
        cookie = build_refresh_cookie_value("fake-id", "fake-secret")
        _set_refresh_cookie(client, cookie)
        resp = client.post("/api/auth/refresh")
        assert resp.status_code == 401


class TestLogoutClearsCookie:
    def test_logout_deletes_cookie(self, client, member):
        token = _login(client)
        resp = client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        cookie_header = resp.headers.get("set-cookie", "")
        assert "refresh_token" in cookie_header
