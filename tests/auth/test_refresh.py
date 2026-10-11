import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import bcrypt
import jwt
import pytest

from app.core.rate_limit import limiter
from app.core.security import (
    ALGORITHM,
    SECRET_KEY,
    build_refresh_cookie_value,
    generate_refresh_secret,
    hash_refresh_secret,
)
from app.models.auth_session import AuthSession
from app.models.member import Member
from app.models.members_oauth2binding import MembersOauth2Binding
from app.services.auth_service import (
    logout_user,
    refresh_session,
    unlink_google_account,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

PASSWORD = "testpass123"
HASHED = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()


@pytest.fixture(autouse=True)
def _reset_rate_limit():
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
            db_session.query(AuthSession)
            .filter(AuthSession.member_id == member.id)
            .count()
        )
        assert sessions == 0


class TestRefreshKeepsTheSessionId:
    def test_the_cookie_keeps_the_session_id_when_the_secret_rotates(
        self, client, member
    ):
        _login(client)
        id_before, secret_before = _get_refresh_cookie(client).split(":", 1)
        client.post("/api/auth/refresh")
        id_after, secret_after = _get_refresh_cookie(client).split(":", 1)
        assert id_after == id_before
        assert secret_after != secret_before

    def test_the_session_id_comes_from_the_stored_row_not_from_the_request(
        self, monkeypatch
    ):
        stored = AuthSession(
            jti="stored-id",
            member_id=uuid.uuid4(),
            refresh_token_hash="unused",
            last_used_at=datetime.now(UTC),
        )
        db = MagicMock()
        filtered = db.query.return_value.filter.return_value
        filtered.with_for_update.return_value.first.return_value = stored
        filtered.first.return_value = Member(email="stub@test.at", auth_locked=False)
        monkeypatch.setattr(
            "app.services.auth_service._validate_refresh_token", lambda *_: None
        )
        monkeypatch.setattr(
            "app.services.auth_service._validate_session_expiry", lambda *_: None
        )

        access_token, session_id, _ = refresh_session(
            db, "id-from-the-request", "ignored"
        )

        assert session_id == "stored-id"
        claims = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
        assert claims["jti"] == "stored-id"


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
        session = AuthSession(
            member_id=member.id,
            jti="expired-session",
            refresh_token_hash=hash_refresh_secret(secret),
            last_used_at=datetime.now(UTC),
            created_at=datetime.now(UTC) - timedelta(days=8),
            updated_at=datetime.now(UTC),
        )
        db_session.add(session)
        db_session.commit()
        cookie = build_refresh_cookie_value("expired-session", secret)
        _set_refresh_cookie(client, cookie)
        resp = client.post("/api/auth/refresh")
        assert resp.status_code == 401

    def test_idle_timeout_returns_401(self, client, member, db_session):
        secret = generate_refresh_secret()
        session = AuthSession(
            member_id=member.id,
            jti="idle-session",
            refresh_token_hash=hash_refresh_secret(secret),
            last_used_at=datetime(2020, 1, 1, tzinfo=UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(session)
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

    def test_a_member_without_an_email_address_is_refused_and_logged_out(
        self, client, member, db_session
    ):
        _login(client)
        member.email = None
        db_session.commit()

        resp = client.post("/api/auth/refresh")

        assert resp.status_code == 401
        assert db_session.query(AuthSession).filter_by(member_id=member.id).count() == 0

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


class TestAuthSessionUuidDefault:
    """Guards the UUID-PK migration's Final-Cutover assumption (see
    bc095b5fb813_sessions_oauth2bindings_summary_orders_.py): id is
    generated exclusively by the database's uuidv7() server_default, not
    by any Python-side default."""

    def test_id_defaults_to_a_valid_uuid7(self, db_session, member):
        session = AuthSession(member_id=member.id, jti="uuid-default-guard")
        db_session.add(session)
        db_session.flush()

        assert isinstance(session.id, uuid.UUID)
        assert session.id.version == 7


def _signed_token(**claims):
    return jwt.encode(claims, SECRET_KEY, algorithm=ALGORITHM)


class TestLogoutEdgeCases:
    """A logout never fails and never touches anything it cannot match."""

    def _session_count(self, db_session):
        db_session.expire_all()
        return db_session.query(AuthSession).count()

    def test_a_token_without_a_session_id_changes_nothing(
        self, client, member, db_session
    ):
        _login(client)
        before = self._session_count(db_session)

        logout_user(db_session, _signed_token(sub="refresh@test.at"))

        assert self._session_count(db_session) == before
        assert member.auth_lastlogout is None

    def test_a_token_for_an_unknown_session_changes_nothing(
        self, client, member, db_session
    ):
        _login(client)
        before = self._session_count(db_session)

        logout_user(db_session, _signed_token(jti="no-such-session"))

        assert self._session_count(db_session) == before
        assert member.auth_lastlogout is None

    def test_a_token_that_is_not_a_token_is_ignored(self, db_session):
        logout_user(db_session, "garbage")

    def test_a_valid_logout_removes_the_session_and_records_the_time(
        self, client, member, db_session
    ):
        token = _login(client)

        logout_user(db_session, token)

        db_session.refresh(member)
        assert self._session_count(db_session) == 0
        assert member.auth_lastlogout is not None


class TestUnlinkGoogleAccount:
    def _bind(self, db_session, member, remote_id):
        db_session.add(
            MembersOauth2Binding(
                member_id=member.id,
                provider="google",
                remote_id=remote_id,
                remote_name="test",
            )
        )
        db_session.commit()

    def _bindings(self, db_session, member):
        db_session.expire_all()
        return (
            db_session.query(MembersOauth2Binding)
            .filter_by(member_id=member.id)
            .count()
        )

    def test_the_route_removes_only_the_callers_link(self, client, member, db_session):
        other = Member(
            email="other@test.at", auth_password=HASHED, vorname="O", nachname="T"
        )
        db_session.add(other)
        db_session.commit()
        self._bind(db_session, member, "mine")
        self._bind(db_session, other, "theirs")
        token = _login(client)

        resp = client.delete(
            "/api/auth/google/link", headers={"Authorization": f"Bearer {token}"}
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "status": "ok",
            "message": "Google-Verknüpfung wurde erfolgreich gelöst.",
        }
        assert self._bindings(db_session, member) == 0
        assert self._bindings(db_session, other) == 1

    def test_unlinking_without_a_link_is_not_an_error(self, client, member, db_session):
        token = _login(client)

        resp = client.delete(
            "/api/auth/google/link", headers={"Authorization": f"Bearer {token}"}
        )

        assert resp.status_code == 200
        assert self._bindings(db_session, member) == 0

    def test_the_route_requires_a_login(self, client, member):
        client.cookies.jar.clear()

        assert client.delete("/api/auth/google/link").status_code == 401

    def test_the_service_leaves_other_members_links_alone(self, member, db_session):
        other = Member(
            email="other2@test.at", auth_password=HASHED, vorname="O", nachname="T"
        )
        db_session.add(other)
        db_session.commit()
        self._bind(db_session, member, "a")
        self._bind(db_session, other, "b")

        unlink_google_account(db_session, member.id)

        assert self._bindings(db_session, member) == 0
        assert self._bindings(db_session, other) == 1
