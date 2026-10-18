"""The bcrypt cost of new hashes follows BCRYPT_ROUNDS, except in production,
where it never drops below 12. Stored hashes keep the cost they were made with.
"""

import bcrypt
import pytest

from app.core import security
from app.core.config import get_settings
from app.core.security import (
    BCRYPT_PRODUCTION_MIN_ROUNDS,
    dummy_password_hash,
    effective_bcrypt_rounds,
    get_password_hash,
    verify_password,
)


@pytest.fixture(autouse=True)
def _fresh_dummy_hash():
    """The dummy hash is cached per process; keep these tests' costs from
    leaking into the tests that verify against it."""
    dummy_password_hash.cache_clear()
    yield
    dummy_password_hash.cache_clear()


def _configure(monkeypatch, *, environment: str, rounds: int) -> None:
    settings = get_settings().model_copy(
        update={"app_environment": environment, "bcrypt_rounds": rounds}
    )
    monkeypatch.setattr(security, "_settings", settings)


class TestEffectiveRounds:
    @pytest.mark.parametrize(
        ("configured", "expected"), [(4, 12), (11, 12), (12, 12), (13, 13), (16, 16)]
    )
    def test_production_never_goes_below_the_minimum(self, configured, expected):
        assert effective_bcrypt_rounds(configured, "production") == expected

    @pytest.mark.parametrize("environment", ["development", "test", "qa", None])
    def test_other_environments_use_the_configured_value(self, environment):
        assert effective_bcrypt_rounds(4, environment) == 4
        assert effective_bcrypt_rounds(16, environment) == 16

    def test_the_minimum_is_twelve(self):
        assert BCRYPT_PRODUCTION_MIN_ROUNDS == 12


class TestPasswordHash:
    def test_production_clamps_a_low_setting_to_cost_12(self, monkeypatch):
        _configure(monkeypatch, environment="production", rounds=4)

        assert get_password_hash("a-long-enough-secret").startswith("$2b$12$")

    def test_outside_production_the_configured_cost_shows_in_the_prefix(
        self, monkeypatch
    ):
        _configure(monkeypatch, environment="development", rounds=5)

        assert get_password_hash("a-long-enough-secret").startswith("$2b$05$")

    def test_the_test_environment_runs_at_the_cheapest_cost(self):
        assert get_password_hash("a-long-enough-secret").startswith("$2b$04$")

    def test_a_stored_hash_keeps_its_cost_when_production_verifies_it(
        self, monkeypatch
    ):
        stored = bcrypt.hashpw(b"secret", bcrypt.gensalt(rounds=4)).decode()
        _configure(monkeypatch, environment="production", rounds=4)

        assert stored.startswith("$2b$04$")
        assert verify_password("secret", stored) is True
        assert verify_password("other", stored) is False


class TestDummyHash:
    def test_follows_the_configured_cost(self, monkeypatch):
        _configure(monkeypatch, environment="development", rounds=5)

        assert dummy_password_hash().startswith("$2b$05$")

    def test_production_clamps_it_like_real_hashes(self, monkeypatch):
        _configure(monkeypatch, environment="production", rounds=4)

        assert dummy_password_hash().startswith("$2b$12$")

    def test_is_created_once_and_matches_no_password(self):
        first = dummy_password_hash()

        assert dummy_password_hash() is first
        assert bcrypt.checkpw(b"anything", first.encode()) is False


class TestSetting:
    def test_defaults_to_twelve_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("BCRYPT_ROUNDS", raising=False)

        assert get_settings().bcrypt_rounds == 12

    def test_the_test_session_configures_the_cheapest_cost(self):
        assert get_settings().bcrypt_rounds == 4
