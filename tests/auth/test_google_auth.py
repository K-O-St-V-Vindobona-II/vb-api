from unittest.mock import patch

import bcrypt
import pytest

from app.models.member import Member
from app.models.members_oauth2binding import MembersOauth2Binding


@pytest.fixture
def test_member_unbound(db_session):
    """
    Creates a user that exists in the main table
    but does not have a Google binding yet.
    """
    hashed = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode("utf-8")
    user = Member(
        email="google.tester@vindobona.at",
        auth_password=hashed,
        auth_locked=False,
    )
    db_session.add(user)
    db_session.commit()
    return user


@patch("app.services.auth_service.id_token.verify_oauth2_token")
def test_google_login_unlinked_returns_404(
    mock_verify,
    client,
    monkeypatch,
    test_member_unbound,  # noqa: ARG001
):
    """
    Tests that a valid Google token WITHOUT a binding
    returns 404 (prompting the frontend to link).
    """
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-client-id-123")
    mock_verify.return_value = {"sub": "google-remote-id-999", "name": "Google Tester"}

    response = client.post(
        "/api/auth/google",
        json={"credential": "super.fake.jwt.token"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "ACCOUNT_NOT_LINKED"


@patch("app.services.auth_service.id_token.verify_oauth2_token")
def test_google_link_success(
    mock_verify,
    client,
    monkeypatch,
    db_session,
    test_member_unbound,
):
    """Tests the actual linking process using local credentials and a Google token."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-client-id-123")
    mock_verify.return_value = {"sub": "google-remote-id-999", "name": "Google Tester"}

    response = client.post(
        "/api/auth/google/link",
        json={
            "credential": "super.fake.jwt.token",
            "email": test_member_unbound.email,
            "password": "secret",
        },
    )

    assert response.status_code == 200
    assert "access_token" in response.json()

    # Verify the binding was actually written to the database
    binding = (
        db_session.query(MembersOauth2Binding)
        .filter_by(member_id=test_member_unbound.id)
        .first()
    )
    assert binding is not None
    assert binding.remote_id == "google-remote-id-999"


def test_google_login_no_client_id(client, monkeypatch):
    """Test what happens if the server admin forgot to set the Google Client ID."""
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    resp = client.post("/api/auth/google", json={"credential": "token"})
    assert resp.status_code == 401
    assert "konfiguriert" in resp.json()["detail"].lower()


@patch("app.services.auth_service.id_token.verify_oauth2_token")
def test_google_login_invalid_token(mock_verify, client, monkeypatch):
    """Test cryptographic failure of the Google token."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake")
    mock_verify.side_effect = ValueError("Invalid signature")
    resp = client.post("/api/auth/google", json={"credential": "bad"})
    assert resp.status_code == 401
    assert "ungültig" in resp.json()["detail"].lower()


@patch("app.services.auth_service.id_token.verify_oauth2_token")
def test_google_login_locked_existing_binding(
    mock_verify, client, monkeypatch, db_session
):
    """Test if a user who was locked AFTER linking their Google account is blocked."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake")
    user = Member(email="lockedb@vindobona.at", auth_locked=True)
    db_session.add(user)
    db_session.commit()

    binding = MembersOauth2Binding(
        member_id=user.id,
        provider="google",
        remote_id="999",
        remote_name="test",
    )
    db_session.add(binding)
    db_session.commit()

    mock_verify.return_value = {"sub": "999", "email": "lockedb@vindobona.at"}
    resp = client.post("/api/auth/google", json={"credential": "token"})
    assert resp.status_code == 401
    assert "gesperrt" in resp.json()["detail"].lower()
