from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import bcrypt
import jwt
import pytest

from app.core.rate_limit import limiter
from app.core.security import ALGORITHM, SECRET_KEY, SESSION_IDLE_TIMEOUT_MINUTES
from app.models.member import Member
from app.models.password_reset import PasswordResetToken
from app.models.personal_access_token import PersonalAccessToken
from app.services.auth_service import create_user_session


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """
    Runs automatically before every test in this file.
    Wipes the in-memory rate limiter so the brute-force test
    doesn't block the legitimate login tests that run afterwards.
    """
    limiter._storage.reset()
    return


@pytest.fixture
def auth_headers(client, db_session):
    """Creates a user, logs them in, and returns the Authorization header."""
    password = "testpassword"
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    user = Member(
        email="logout.tester@vindobona.at",
        auth_password=hashed,
        auth_locked=False,
        org_id="vbw",
    )
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/api/auth/login",
        data={"username": user.email, "password": password},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_logout_invalidates_session(client, auth_headers):
    """Tests if logging out actually destroys the session in the DB."""
    # 1. Logout
    logout_response = client.post("/api/auth/logout", headers=auth_headers)
    assert logout_response.status_code == 200

    # 2. Try to access a protected route with the now-dead token
    me_response = client.get("/api/members/me", headers=auth_headers)
    assert me_response.status_code == 401


def test_rate_limiter_blocks_brute_force(client, db_session):  # noqa: ARG001
    """Tests if the SlowAPI rate limiter catches spam requests on login."""
    # Our limit is 5/minute. We fire 6 times.
    for _ in range(5):
        client.post(
            "/api/auth/login",
            data={
                "username": "spam@test.com",
                "password": "wrong",
            },
        )

    response = client.post(
        "/api/auth/login",
        data={
            "username": "spam@test.com",
            "password": "wrong",
        },
    )
    assert response.status_code == 429  # Too Many Requests


@patch("app.services.auth_service.send_reset_email")
def test_password_reset_flow(mock_send_email, client, db_session):  # noqa: ARG001
    """Tests the entire password reset cycle (forgot -> reset -> new login)."""
    # 1. Arrange: Create user
    hashed = bcrypt.hashpw(b"oldpassword", bcrypt.gensalt()).decode("utf-8")
    user = Member(
        email="reset@vindobona.at",
        auth_password=hashed,
        auth_locked=False,
        org_id="vbw",
    )
    db_session.add(user)
    db_session.commit()

    # 2. Act: Trigger forgot password
    resp1 = client.post(
        "/api/auth/forgot-password",
        json={"email": "reset@vindobona.at"},
    )
    assert resp1.status_code == 200

    # Assert: Check if token was saved to DB
    reset_entry = (
        db_session.query(PasswordResetToken)
        .filter_by(email="reset@vindobona.at")
        .first()
    )
    assert reset_entry is not None

    # 3. Act: Execute password reset
    resp2 = client.post(
        "/api/auth/reset-password",
        json={
            "email": "reset@vindobona.at",
            "token": reset_entry.token,
            "password": "new_super_password",
        },
    )
    assert resp2.status_code == 200

    # 4. Verify: email_verified_at is set after reset
    db_session.refresh(user)
    assert user.email_verified_at is not None

    # 5. Verify: Try to login with the new password
    login_resp = client.post(
        "/api/auth/login",
        data={"username": "reset@vindobona.at", "password": "new_super_password"},
    )
    assert login_resp.status_code == 200
    assert "access_token" in login_resp.json()


def test_password_reset_invalidates_existing_sessions(client, db_session):
    """A password reset must destroy all active sessions of that member."""
    # 1. Arrange: create a user with an active session
    hashed = bcrypt.hashpw(b"oldpassword", bcrypt.gensalt()).decode("utf-8")
    user = Member(
        email="invalidate@vindobona.at",
        auth_password=hashed,
        auth_locked=False,
        org_id="vbw",
    )
    db_session.add(user)
    db_session.commit()

    token, _, _ = create_user_session(db_session, user)
    headers = {"Authorization": f"Bearer {token}"}

    # The session must work before the reset
    assert client.get("/api/members/me", headers=headers).status_code == 200

    # 2. Act: reset the password using a directly created reset token (skips SMTP)
    reset_entry = PasswordResetToken(email=user.email, token="reset-token-123")
    db_session.add(reset_entry)
    db_session.commit()

    resp = client.post(
        "/api/auth/reset-password",
        json={
            "email": user.email,
            "token": "reset-token-123",
            "password": "brand_new_password",
        },
    )
    assert resp.status_code == 200

    # 3. Assert: the previously valid session is now rejected
    assert client.get("/api/members/me", headers=headers).status_code == 401


def test_inactivity_timeout_kicks_user(client, db_session, auth_headers):
    """Tests if a user is kicked out after being idle for too long."""
    # 1. Verify normal access works
    resp_me = client.get("/api/members/me", headers=auth_headers)
    assert resp_me.status_code == 200

    # 2. Artificially age the session in the database
    token_string = auth_headers["Authorization"].split(" ")[1]
    payload = jwt.decode(
        token_string,
        SECRET_KEY,
        algorithms=[ALGORITHM],
        options={"verify_exp": False},
    )
    token_id = payload.get("jti")

    session_record = (
        db_session.query(PersonalAccessToken).filter_by(token=token_id).first()
    )

    # Move last_used_at back in time to simulate inactivity (Timeout + 5 mins)
    past_time = datetime.now(UTC) - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES + 5)
    session_record.last_used_at = past_time
    db_session.commit()

    # 3. Access again -> Should be 401 Unauthorized
    resp_expired = client.get("/api/members/me", headers=auth_headers)
    assert resp_expired.status_code == 401
    assert "inactivity" in resp_expired.json()["detail"].lower()


def test_deps_invalid_jwt(client):
    """Test accessing a guarded route with pure garbage."""
    resp = client.get("/api/members/me", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


def test_deps_deleted_user(client, db_session, auth_headers):
    """Test if a user whose DB record was deleted during an active session is kicked."""
    db_session.query(Member).delete()
    db_session.commit()
    resp = client.get("/api/members/me", headers=auth_headers)
    assert resp.status_code == 401


def test_deps_locked_user(client, db_session):
    """Test if a session is immediately invalidated if the admin locks the account."""
    hashed = bcrypt.hashpw(b"pass", bcrypt.gensalt()).decode("utf-8")
    user = Member(
        email="locked@vindobona.at",
        auth_password=hashed,
        auth_locked=True,
        org_id="vbw",
    )
    db_session.add(user)
    db_session.commit()

    # Bypass the login screen to generate an active session for a locked user
    token, _, _ = create_user_session(db_session, user)

    resp = client.get("/api/members/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert "locked" in resp.json()["detail"].lower()
