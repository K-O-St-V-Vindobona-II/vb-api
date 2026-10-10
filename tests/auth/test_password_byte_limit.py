"""bcrypt works on the first 72 bytes of a password. A longer one must neither
break the sign-in (and flood the log with a stack trace per attempt) nor be
accepted when a new password is chosen."""

from unittest.mock import Mock

import bcrypt
import pytest
from pydantic import ValidationError

from app.core.rate_limit import limiter
from app.core.security import BCRYPT_MAX_PASSWORD_BYTES, verify_password
from app.models.member import Member
from app.schemas.auth import ResetPasswordRequest


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    limiter._storage.reset()


@pytest.fixture
def security_logger(monkeypatch):
    """The module logger as a mock: the logging set-up that the migrations run
    at the start of the test session disables loggers that already exist, so
    caplog would see nothing and every 'no log record' check would pass."""
    logger = Mock()
    monkeypatch.setattr("app.core.security.logger", logger)
    return logger


def _hash(secret: bytes) -> str:
    return bcrypt.hashpw(secret, bcrypt.gensalt()).decode()


def _reset_request(password: str) -> ResetPasswordRequest:
    return ResetPasswordRequest(email="a@vindobona.at", token="t", password=password)


class TestVerifyPassword:
    def test_a_long_password_is_compared_on_its_first_72_bytes(self, security_logger):
        long_password = "p" * 100
        stored = _hash(long_password.encode()[:BCRYPT_MAX_PASSWORD_BYTES])

        assert verify_password(long_password, stored) is True

        security_logger.exception.assert_not_called()

    def test_a_wrong_long_password_is_refused_without_a_log_record(
        self, security_logger
    ):
        stored = _hash(b"p" * 72)

        assert verify_password("q" * 100, stored) is False

        security_logger.exception.assert_not_called()

    def test_bytes_after_the_72nd_do_not_matter(self):
        stored = _hash(b"a" * 72)

        assert verify_password("a" * 72 + "anything else", stored) is True

    def test_a_multibyte_character_across_the_boundary_is_cut_like_the_stored_hash(
        self,
    ):
        password = "a" * 71 + "ä" + "tail"
        stored = _hash(password.encode()[:BCRYPT_MAX_PASSWORD_BYTES])

        assert verify_password(password, stored) is True

    def test_a_short_password_works_as_before(self):
        stored = _hash(b"short-secret")

        assert verify_password("short-secret", stored) is True
        assert verify_password("short-secreT", stored) is False

    def test_exactly_72_bytes_work(self):
        stored = _hash(b"x" * 72)

        assert verify_password("x" * 72, stored) is True

    def test_a_hash_with_the_2y_prefix_is_still_accepted(self):
        stored = _hash(b"prefixed").replace("$2b$", "$2y$", 1)

        assert verify_password("prefixed", stored) is True

    def test_an_unreadable_hash_is_refused_and_still_logged(self, security_logger):
        assert verify_password("whatever", "not-a-bcrypt-hash") is False

        security_logger.exception.assert_called_once()

    def test_an_empty_hash_is_refused_quietly(self, security_logger):
        assert verify_password("whatever", "") is False

        security_logger.exception.assert_not_called()


class TestSignInWithALongPassword:
    def test_the_login_route_accepts_a_password_stored_from_its_first_72_bytes(
        self, client, db_session
    ):
        long_password = "x" * 100
        db_session.add(
            Member(
                email="long.password@vindobona.at",
                auth_password=_hash(long_password.encode()[:BCRYPT_MAX_PASSWORD_BYTES]),
                auth_locked=False,
            )
        )
        db_session.commit()

        response = client.post(
            "/api/auth/login",
            data={"username": "long.password@vindobona.at", "password": long_password},
        )

        assert response.status_code == 200
        assert "access_token" in response.json()

    def test_a_wrong_password_is_still_refused(self, client, db_session):
        db_session.add(
            Member(
                email="long.password2@vindobona.at",
                auth_password=_hash(b"x" * 72),
                auth_locked=False,
            )
        )
        db_session.commit()

        response = client.post(
            "/api/auth/login",
            data={"username": "long.password2@vindobona.at", "password": "y" * 100},
        )

        assert response.status_code == 401


class TestResetPasswordSchema:
    def test_more_than_72_bytes_are_refused_even_below_72_characters(self):
        password = "ä" * 40  # 40 characters, 80 bytes
        assert len(password.encode()) > BCRYPT_MAX_PASSWORD_BYTES

        with pytest.raises(ValidationError, match="Bytes") as info:
            _reset_request(password)

        assert "Umlaute" in str(info.value)

    def test_73_ascii_characters_are_refused(self):
        with pytest.raises(ValidationError, match="höchstens 72 Bytes"):
            _reset_request("a" * 73)

    @pytest.mark.parametrize("password", ["a" * 72, "ä" * 36, "a" * 70 + "ä"])
    def test_exactly_72_bytes_are_accepted(self, password):
        assert len(password.encode()) == BCRYPT_MAX_PASSWORD_BYTES

        assert _reset_request(password).password == password

    def test_the_minimum_length_still_counts_characters(self):
        with pytest.raises(ValidationError, match="mindestens 8 Zeichen"):
            _reset_request("äääääää")  # 7 characters, 14 bytes

        assert _reset_request("äääääääß").password == "äääääääß"

    def test_the_limit_is_part_of_the_route_answer(self, client):
        response = client.post(
            "/api/auth/reset-password",
            json={"email": "a@vindobona.at", "token": "t", "password": "ä" * 40},
        )

        assert response.status_code == 422
        assert "Bytes" in str(response.json()["detail"])
