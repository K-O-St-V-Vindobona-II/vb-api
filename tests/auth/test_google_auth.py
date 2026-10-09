import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import bcrypt
import pytest
from google.auth.exceptions import TransportError

from app.core.rate_limit import limiter
from app.models.enums import OauthProvider
from app.models.member import Member
from app.models.members_oauth2binding import MembersOauth2Binding
from app.services import auth_service
from app.services.auth_service import _GOOGLE_CERTS_TIMEOUT_SECONDS, _TimeoutHTTPAdapter


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
    returns 404 (asking the frontend to link).
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
def test_google_login_unavailable_returns_503(mock_verify, client, monkeypatch):
    """Regression test: a network/timeout failure while fetching Google's
    certs must surface as a clear 503, not as a hang or a generic 500 -
    the root cause of the prod incident this test guards against."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake")
    mock_verify.side_effect = TransportError("Connection timed out")

    resp = client.post("/api/auth/google", json={"credential": "token"})

    assert resp.status_code == 503
    assert "nicht erreichbar" in resp.json()["detail"].lower()


@patch("app.services.auth_service.id_token.verify_oauth2_token")
def test_google_link_unavailable_returns_503(
    mock_verify, client, monkeypatch, test_member_unbound
):
    """Same regression as above, for the /google/link endpoint."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake")
    mock_verify.side_effect = TransportError("Connection timed out")

    resp = client.post(
        "/api/auth/google/link",
        json={
            "credential": "token",
            "email": test_member_unbound.email,
            "password": "secret",
        },
    )

    assert resp.status_code == 503
    assert "nicht erreichbar" in resp.json()["detail"].lower()


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


def _login_via_google(db):
    """Mirrors what the router does: authenticate, then create the
    session - two service calls orchestrated by the caller, not one
    function calling the other."""
    member = auth_service.authenticate_google_user(db, "fake-token")
    auth_service.create_user_session(db, member)


def _link_via_google(db, email):
    member = auth_service.link_google_account(db, "fake-token", email, "secret")
    auth_service.create_user_session(db, member)


class TestGoogleLoginAtomicity:
    """Regression tests: authenticate_google_user()/
    link_google_account() used to commit their binding update on their
    own, separately from create_user_session()'s commit - two commits for
    one logical "log the user in" operation. Now the binding update only
    flushes; create_user_session()'s commit is the only one."""

    @patch("app.services.auth_service.id_token.verify_oauth2_token")
    def test_login_rolls_back_binding_update_if_session_creation_fails(
        self, mock_verify, db_session, monkeypatch
    ):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake")
        user = Member(email="atomicity@vindobona.at", auth_locked=False)
        db_session.add(user)
        db_session.commit()

        original_lastuse = datetime(2020, 1, 1, tzinfo=UTC)
        binding = MembersOauth2Binding(
            member_id=user.id,
            provider="google",
            remote_id="atomicity-remote-id",
            remote_name="test",
            lastuse_at=original_lastuse,
        )
        db_session.add(binding)
        db_session.commit()

        mock_verify.return_value = {"sub": "atomicity-remote-id"}

        with (
            patch(
                "app.services.auth_service.create_user_session",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError),
        ):
            _login_via_google(db_session)

        db_session.rollback()
        db_session.refresh(binding)
        assert binding.lastuse_at == original_lastuse

    @patch("app.services.auth_service.id_token.verify_oauth2_token")
    def test_link_rolls_back_new_binding_if_session_creation_fails(
        self, mock_verify, db_session, monkeypatch
    ):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake")
        hashed = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode("utf-8")
        user = Member(
            email="link-atomicity@vindobona.at",
            auth_password=hashed,
            auth_locked=False,
        )
        db_session.add(user)
        db_session.commit()

        mock_verify.return_value = {"sub": "link-atomicity-remote-id", "name": "Test"}

        with (
            patch(
                "app.services.auth_service.create_user_session",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError),
        ):
            _link_via_google(db_session, user.email)

        db_session.rollback()
        assert (
            db_session.query(MembersOauth2Binding).filter_by(member_id=user.id).count()
            == 0
        )


class TestTimeoutHTTPAdapter:
    """Regression tests for the transport-level timeout fix: without it,
    a stalled connection to Google's certs endpoint blocks for
    google-auth's own internal default of 120 seconds (per attempted
    address) instead of failing fast - root cause of the prod Google-Login
    hang this fix guards against."""

    def test_applies_default_timeout_when_none_given(self):
        adapter = _TimeoutHTTPAdapter()

        with patch(
            "requests.adapters.HTTPAdapter.send", return_value=MagicMock()
        ) as mock_send:
            adapter.send(MagicMock())

        assert mock_send.call_args.kwargs["timeout"] == _GOOGLE_CERTS_TIMEOUT_SECONDS

    def test_preserves_explicit_timeout(self):
        adapter = _TimeoutHTTPAdapter()

        with patch(
            "requests.adapters.HTTPAdapter.send", return_value=MagicMock()
        ) as mock_send:
            adapter.send(MagicMock(), timeout=42.0)

        assert mock_send.call_args.kwargs["timeout"] == 42.0


class TestMembersOauth2BindingUuidDefault:
    """Guards the UUID-PK migration's Final-Cutover assumption (see
    bc095b5fb813_sessions_oauth2bindings_summary_orders_.py): id is
    generated exclusively by the database's uuidv7() server_default, not
    by any Python-side default."""

    def test_id_defaults_to_a_valid_uuid7(self, db_session):
        member = Member(vorname="Test", nachname="User")
        db_session.add(member)
        db_session.commit()

        binding = MembersOauth2Binding(
            member_id=member.id,
            provider="google",
            remote_id="uuid-default-guard",
            remote_name="Guard",
        )
        db_session.add(binding)
        db_session.flush()

        assert isinstance(binding.id, uuid.UUID)
        assert binding.id.version == 7


LINK_URL = "/api/auth/google/link"
LOGIN_URL = "/api/auth/google"


LOCAL_PASSWORD = "secret"
BAD_SUBJECT_CLAIMS = [{}, {"sub": None}, {"sub": ""}, {"sub": 12345}, {"sub": ["a"]}]


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The sign-in and link routes allow a few calls per minute; every test
    here makes its own."""
    limiter._storage.reset()


def _link_payload(member, password=None):
    return {
        "credential": "token",
        "email": member.email,
        "password": LOCAL_PASSWORD if password is None else password,
    }


@patch("app.services.auth_service.id_token.verify_oauth2_token")
class TestLinkFailsClosedWithoutClientId:
    def test_the_token_is_not_even_verified(
        self, mock_verify, client, monkeypatch, db_session, test_member_unbound
    ):
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        mock_verify.return_value = {"sub": "other-application-user"}

        resp = client.post(LINK_URL, json=_link_payload(test_member_unbound))

        assert resp.status_code == 401
        assert "konfiguriert" in resp.json()["detail"].lower()
        mock_verify.assert_not_called()
        assert db_session.query(MembersOauth2Binding).count() == 0

    def test_an_empty_client_id_counts_as_unset(
        self, mock_verify, client, monkeypatch, test_member_unbound
    ):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "")

        resp = client.post(LINK_URL, json=_link_payload(test_member_unbound))

        assert resp.status_code == 401
        mock_verify.assert_not_called()

    def test_wrong_local_credentials_are_still_reported_first(
        self, mock_verify, client, monkeypatch, test_member_unbound
    ):
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)

        resp = client.post(
            LINK_URL, json=_link_payload(test_member_unbound, password="wrong")
        )

        assert resp.status_code == 401
        assert "konfiguriert" not in resp.json()["detail"].lower()
        mock_verify.assert_not_called()


@patch("app.services.auth_service.id_token.verify_oauth2_token")
class TestAudienceIsAlwaysChecked:
    def test_login_passes_the_client_id_as_audience(
        self, mock_verify, client, monkeypatch
    ):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "the-client-id")
        mock_verify.return_value = {"sub": "unknown-google-user"}

        client.post(LOGIN_URL, json={"credential": "token"})

        assert mock_verify.call_args.args[2] == "the-client-id"

    def test_link_passes_the_client_id_as_audience(
        self, mock_verify, client, monkeypatch, test_member_unbound
    ):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "the-client-id")
        mock_verify.return_value = {"sub": "new-google-user", "name": "Name"}

        resp = client.post(LINK_URL, json=_link_payload(test_member_unbound))

        assert resp.status_code == 200
        assert mock_verify.call_args.args[2] == "the-client-id"


@patch("app.services.auth_service.id_token.verify_oauth2_token")
class TestSubjectClaim:
    @pytest.mark.parametrize("claims", BAD_SUBJECT_CLAIMS)
    def test_login_rejects_a_token_without_a_usable_subject(
        self, mock_verify, client, monkeypatch, claims
    ):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake")
        mock_verify.return_value = claims

        resp = client.post(LOGIN_URL, json={"credential": "token"})

        assert resp.status_code == 401
        assert "ungültig" in resp.json()["detail"].lower()

    @pytest.mark.parametrize("claims", BAD_SUBJECT_CLAIMS)
    def test_link_rejects_a_token_without_a_usable_subject(
        self, mock_verify, client, monkeypatch, db_session, test_member_unbound, claims
    ):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake")
        mock_verify.return_value = claims

        resp = client.post(LINK_URL, json=_link_payload(test_member_unbound))

        assert resp.status_code == 401
        assert "ungültig" in resp.json()["detail"].lower()
        assert db_session.query(MembersOauth2Binding).count() == 0


@patch("app.services.auth_service.id_token.verify_oauth2_token")
class TestVisibleMessagesStayTheSame:
    def test_login_names_an_invalid_token_as_before(
        self, mock_verify, client, monkeypatch
    ):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake")
        mock_verify.side_effect = ValueError("bad signature")

        resp = client.post(LOGIN_URL, json={"credential": "token"})

        assert resp.json()["detail"] == "Ungültiger Google-Token."

    def test_link_names_an_invalid_token_as_before(
        self, mock_verify, client, monkeypatch, test_member_unbound
    ):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake")
        mock_verify.side_effect = ValueError("bad signature")

        resp = client.post(LINK_URL, json=_link_payload(test_member_unbound))

        assert resp.json()["detail"] == "Der Google-Token ist ungültig oder abgelaufen."

    @pytest.mark.parametrize("claims", [{"sub": "g-1"}, {"sub": "g-1", "name": 7}])
    def test_link_falls_back_to_a_placeholder_name(
        self, mock_verify, client, monkeypatch, db_session, test_member_unbound, claims
    ):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake")
        mock_verify.return_value = claims

        resp = client.post(LINK_URL, json=_link_payload(test_member_unbound))

        assert resp.status_code == 200
        binding = db_session.query(MembersOauth2Binding).one()
        assert binding.remote_name == "Unknown"


@patch("app.services.auth_service.id_token.verify_oauth2_token")
def test_google_login_of_a_member_without_an_address_is_a_401(
    mock_verify, client, monkeypatch, db_session
):
    """A bound member without an e-mail address cannot get an access token
    (its subject is the address); the answer is a 401, not an unhandled 500."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-client-id-123")
    mock_verify.return_value = {"sub": "google-remote-id-777", "name": "No Address"}
    member = Member(email=None, auth_locked=False)
    db_session.add(member)
    db_session.commit()
    db_session.add(
        MembersOauth2Binding(
            member_id=member.id,
            provider=OauthProvider.GOOGLE,
            remote_id="google-remote-id-777",
            remote_name="No Address",
        )
    )
    db_session.commit()

    response = client.post("/api/auth/google", json={"credential": "x.y.z"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Member hat keine E-Mail-Adresse."}
