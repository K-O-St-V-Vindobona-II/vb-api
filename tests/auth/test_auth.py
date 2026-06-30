from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import bcrypt
import pytest

from app.core.security import create_access_token, verify_password
from app.models.member import Member
from app.models.password_reset import PasswordResetToken
from app.services.auth_service import logout_user


@pytest.fixture
def test_user(db_session):
    """Creates a valid test user in the volatile in-memory DB."""
    password = "secretpassword"
    # Hash password cleanly, exactly as our system expects it
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    user = Member(
        email="test@vindobona.at", auth_password=hashed, auth_locked=False, org_id="vbw"
    )
    db_session.add(user)
    db_session.commit()

    # Return the object and the cleartext password so the test can log in
    return user, password


def test_login_success(client, test_user):
    user, plain_password = test_user

    # Important: OAuth2 expects form data ('data='), not JSON ('json=')!
    response = client.post(
        "/api/auth/login", data={"username": user.email, "password": plain_password}
    )

    assert response.status_code == 200
    assert "access_token" in response.json()
    assert response.json()["token_type"] == "bearer"


def test_login_wrong_password(client, test_user):
    user, _ = test_user
    response = client.post(
        "/api/auth/login", data={"username": user.email, "password": "wrongpassword"}
    )
    assert response.status_code == 401
    data = response.json()
    assert data["failure_reason"] == "wrong_password"
    assert data["attempted_email"] is None


def test_login_unknown_email(client, test_user):  # noqa: ARG001
    response = client.post(
        "/api/auth/login",
        data={"username": "nonexistent@nowhere.at", "password": "whatever"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["failure_reason"] == "unknown_email"
    assert data["attempted_email"] == "nonexistent@nowhere.at"


def test_verify_password_edge_cases():
    """Tests the hash verification edge cases including Laravel legacy hashes."""
    # 1. Missing hash
    assert verify_password("pass", None) is False

    # 2. Legacy Laravel $2y$ hash (valid hash for the word 'password')
    legacy_hash = "$2y$10$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uheWG/igi"
    assert verify_password("password", legacy_hash) is True

    # 3. Invalid format causing bcrypt exception
    assert verify_password("pass", "invalid_hash_format") is False


def test_create_access_token_with_delta():
    """Tests token generation with a custom expiration delta."""
    token, _jti = create_access_token("test", expires_delta=timedelta(minutes=5))
    assert token is not None


@patch("app.services.auth_service.send_reset_email")
def test_forgot_password_unknown_email(mock_send_email, client, db_session):  # noqa: ARG001
    """Ensure unknown emails are silently ignored to prevent enumeration."""
    resp = client.post(
        "/api/auth/forgot-password",
        json={"email": "nobody@nowhere.com"},
    )
    assert resp.status_code == 200


def test_reset_password_invalid_token(client, db_session):  # noqa: ARG001
    """Tests if tampering with the reset token is blocked."""
    resp = client.post(
        "/api/auth/reset-password",
        json={
            "email": "test@vindobona.at",
            "token": "bad",
            "password": "new_valid_password",
        },
    )
    assert resp.status_code == 400
    assert "ungültig" in resp.json()["detail"].lower()


def test_reset_password_expired_token(client, db_session):
    """Tests if a token older than 20 minutes is rejected."""
    past = datetime.now(UTC) - timedelta(minutes=25)
    token = PasswordResetToken(
        email="expired@vindobona.at",
        token="exp_token",
        created_at=past,
    )
    db_session.add(token)
    db_session.commit()

    resp = client.post(
        "/api/auth/reset-password",
        json={
            "email": "expired@vindobona.at",
            "token": "exp_token",
            "password": "new_valid_password",
        },
    )
    assert resp.status_code == 400
    assert "abgelaufen" in resp.json()["detail"].lower()


def test_reset_password_user_deleted(client, db_session):
    """Tests reset when the user was deleted while holding a valid token."""
    token = PasswordResetToken(email="ghost@vindobona.at", token="ghost_token")
    db_session.add(token)
    db_session.commit()

    resp = client.post(
        "/api/auth/reset-password",
        json={
            "email": "ghost@vindobona.at",
            "token": "ghost_token",
            "password": "new_valid_password",
        },
    )
    assert resp.status_code == 400
    assert "nicht gefunden" in resp.json()["detail"].lower()


def test_logout_with_garbage_token(db_session):
    """Ensure invalid tokens in logout attempt don't crash the server."""
    logout_user(db_session, "garbage_token_string")


class TestAuthTimestamps:
    def test_login_sets_lastlogin(self, client, test_user, db_session):
        user, pw = test_user
        assert user.auth_lastlogin is None
        client.post(
            "/api/auth/login",
            data={
                "username": user.email,
                "password": pw,
            },
        )
        db_session.refresh(user)
        assert user.auth_lastlogin is not None

    def test_google_login_sets_lastlogin(self, db_session):
        from app.services.auth_service import create_user_session

        hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
        m = Member(
            email="google@vbw.at",
            auth_password=hashed,
            auth_locked=False,
            org_id="vbw",
        )
        db_session.add(m)
        db_session.commit()
        assert m.auth_lastlogin is None
        create_user_session(db_session, m)
        db_session.refresh(m)
        assert m.auth_lastlogin is not None

    def test_refresh_sets_lastsignal(self, client, test_user, db_session):
        user, pw = test_user
        client.cookies.jar.clear()
        client.post(
            "/api/auth/login",
            data={
                "username": user.email,
                "password": pw,
            },
        )
        db_session.refresh(user)
        assert user.auth_lastsignal is None

        client.post("/api/auth/refresh")
        db_session.refresh(user)
        assert user.auth_lastsignal is not None

    def test_logout_sets_lastlogout(self, client, test_user, db_session):
        from app.services.auth_service import create_user_session

        user, _ = test_user
        token, _, _ = create_user_session(db_session, user)
        assert user.auth_lastlogout is None

        client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        db_session.refresh(user)
        assert user.auth_lastlogout is not None
