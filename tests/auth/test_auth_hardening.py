"""Regression tests for the hardening of the authentication core.

Each class pins one property that an earlier implementation lacked: an opaque
login failure, digest-only reset tokens, throttled unauthenticated endpoints
and a cached, time-bounded download of Google's signing certificates.
"""

from unittest.mock import MagicMock, patch

import bcrypt
import pytest
import requests

from app.core import security
from app.core.rate_limit import limiter
from app.core.security import verify_password
from app.models.member import Member
from app.models.password_reset import PasswordResetToken
from app.services import auth_service


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    limiter._storage.reset()


_PASSWORD = "secretpassword"


def _make_member(db, email: str, *, locked=False):
    hashed = bcrypt.hashpw(_PASSWORD.encode(), bcrypt.gensalt()).decode()
    member = Member(email=email, auth_password=hashed, auth_locked=locked)
    db.add(member)
    db.commit()
    return member


class TestLoginDoesNotLeakAccountState:
    @pytest.fixture
    def accounts(self, db_session):
        _make_member(db_session, "active@vindobona.at")
        _make_member(db_session, "locked@vindobona.at", locked=True)

    CASES = (
        ("unknown@vindobona.at", "secretpassword"),
        ("active@vindobona.at", "wrong-password"),
        ("locked@vindobona.at", "secretpassword"),
    )

    def test_failure_response_is_identical_for_every_reason(self, client, accounts):
        bodies = []
        for username, password in self.CASES:
            resp = client.post(
                "/api/auth/login", data={"username": username, "password": password}
            )
            assert resp.status_code == 401
            bodies.append(resp.json())

        assert bodies[0] == bodies[1] == bodies[2]
        assert set(bodies[0]) == {"detail"}

    @pytest.mark.parametrize(("username", "password"), CASES)
    def test_exactly_one_bcrypt_comparison_runs_per_attempt(
        self, db_session, accounts, username, password
    ):
        with patch(
            "app.services.auth_service.verify_password", return_value=False
        ) as mock_verify:
            member, _ = auth_service.authenticate_user(db_session, username, password)

        assert member is None
        mock_verify.assert_called_once()

    def test_unknown_email_is_checked_against_the_dummy_hash(self, db_session):
        with patch(
            "app.services.auth_service.verify_password", return_value=False
        ) as mock_verify:
            auth_service.authenticate_user(db_session, "nobody@x.at", "pw")

        assert mock_verify.call_args.args[1] == auth_service._DUMMY_PASSWORD_HASH

    def test_account_without_a_password_is_checked_against_the_dummy_hash(
        self, db_session
    ):
        db_session.add(
            Member(
                email="google.only@vindobona.at", auth_password=None, auth_locked=False
            )
        )
        db_session.commit()

        with patch(
            "app.services.auth_service.verify_password", return_value=False
        ) as mock_verify:
            member, reason = auth_service.authenticate_user(
                db_session, "google.only@vindobona.at", "pw"
            )

        assert (member, reason) == (None, "wrong_password")
        assert mock_verify.call_args.args[1] == auth_service._DUMMY_PASSWORD_HASH

    def test_dummy_hash_has_the_production_cost_factor(self):
        assert auth_service._DUMMY_PASSWORD_HASH.startswith("$2b$12$")
        assert (
            bcrypt.checkpw(b"anything", auth_service._DUMMY_PASSWORD_HASH.encode())
            is False
        )

    def test_reason_is_logged_without_the_address(self, db_session, monkeypatch):
        # Mocks the module logger instead of using caplog: alembic's
        # fileConfig() call in the schema fixture disables every logger that
        # already exists, so caplog would never see the record.
        mock_logger = MagicMock()
        monkeypatch.setattr("app.services.auth_service.logger", mock_logger)

        auth_service.authenticate_user(db_session, "secret.person@x.at", "pw")

        mock_logger.info.assert_called_once_with("Login rejected: %s", "unknown_email")

    def test_successful_login_still_returns_the_member(self, db_session):
        member = _make_member(db_session, "active@vindobona.at")

        result, reason = auth_service.authenticate_user(
            db_session, "ACTIVE@vindobona.at", _PASSWORD
        )

        assert result is member
        assert reason == "ok"


class TestResetTokenStorage:
    def test_only_a_digest_of_the_token_is_stored(self, db_session):
        member = _make_member(db_session, "reset.me@vindobona.at")

        result = auth_service.process_forgot_password(db_session, member.email)

        assert result is not None
        _, token = result
        row = db_session.query(PasswordResetToken).one()
        assert row.token_hash != token
        assert row.token_hash == security.hash_reset_token(token)

    def test_stored_digest_cannot_be_replayed_as_the_token(self, db_session):
        member = _make_member(db_session, "reset.me@vindobona.at")
        auth_service.process_forgot_password(db_session, member.email)
        digest = db_session.query(PasswordResetToken).one().token_hash

        with pytest.raises(ValueError, match="Ungültiger Token"):
            auth_service.execute_password_reset(
                db_session, member.email, digest, "a-brand-new-password"
            )

    def test_the_emailed_token_still_resets_the_password(self, db_session):
        member = _make_member(db_session, "reset.me@vindobona.at")
        result = auth_service.process_forgot_password(db_session, member.email)
        assert result is not None

        auth_service.execute_password_reset(
            db_session, member.email, result[1], "a-brand-new-password"
        )

        assert verify_password("a-brand-new-password", member.auth_password)
        assert db_session.query(PasswordResetToken).count() == 0


class TestGoogleEndpointsAreThrottled:
    @patch("app.services.auth_service.id_token.verify_oauth2_token")
    def test_google_login_is_rate_limited(self, mock_verify, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake")
        mock_verify.side_effect = ValueError("bad token")

        statuses = [
            client.post("/api/auth/google", json={"credential": "x"}).status_code
            for _ in range(11)
        ]

        assert statuses[:10] == [401] * 10
        assert statuses[10] == 429

    def test_reset_password_is_rate_limited(self, client):
        payload = {
            "email": "nobody@vindobona.at",
            "token": "t",
            "password": "long-enough-password",
        }

        statuses = [
            client.post("/api/auth/reset-password", json=payload).status_code
            for _ in range(6)
        ]

        assert statuses[:5] == [400] * 5
        assert statuses[5] == 429


class TestCertsCachingRequest:
    @pytest.fixture
    def transport(self):
        session = MagicMock(spec=requests.Session)
        response = MagicMock(status_code=200, headers={}, content=b"{}")
        session.request.return_value = response
        return auth_service._CertsCachingRequest(session=session), session

    def test_repeated_gets_hit_the_network_once(self, transport):
        request, session = transport

        for _ in range(5):
            assert request("https://certs.example/keys").status == 200

        assert session.request.call_count == 1

    def test_cache_expires(self, transport):
        request, session = transport

        with patch("app.services.auth_service.time.monotonic") as clock:
            clock.return_value = 1000.0
            request("https://certs.example/keys")
            clock.return_value = 1000.0 + auth_service._GOOGLE_CERTS_CACHE_SECONDS + 1
            request("https://certs.example/keys")

        assert session.request.call_count == 2

    def test_failed_responses_are_not_cached(self, transport):
        request, session = transport
        session.request.return_value = MagicMock(
            status_code=503, headers={}, content=b""
        )

        request("https://certs.example/keys")
        request("https://certs.example/keys")

        assert session.request.call_count == 2

    def test_default_timeout_is_the_short_one(self, transport):
        request, session = transport

        request("https://certs.example/keys")

        assert (
            session.request.call_args.kwargs["timeout"]
            == auth_service._GOOGLE_CERTS_TIMEOUT_SECONDS
        )

    def test_socket_layer_receives_the_short_timeout(self):
        """google-auth's own transport passes timeout=120 explicitly, which
        used to defeat the adapter's default: check what actually arrives."""
        seen: list[float | None] = []

        def fake_send(_adapter, request, **kwargs):
            seen.append(kwargs.get("timeout"))
            response = requests.Response()
            response.status_code = 200
            response.url = request.url
            return response

        with patch.object(requests.adapters.HTTPAdapter, "send", fake_send):
            auth_service._build_google_auth_request()(
                "https://certs.example/keys", method="GET"
            )

        assert seen == [auth_service._GOOGLE_CERTS_TIMEOUT_SECONDS]

    def test_other_methods_bypass_the_cache(self, transport):
        request, session = transport

        request("https://certs.example/keys", method="POST", body=b"x")
        request("https://certs.example/keys", method="POST", body=b"x")

        assert session.request.call_count == 2
