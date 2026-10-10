"""The documented example environment is the first thing a new developer
copies, so it has to describe the real settings and actually start the app."""

import re
import secrets
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.core.config import Settings

EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
REQUIRED_AT_START = {
    "APP_ENVIRONMENT",
    "CORS_ORIGINS",
    "SECRET_KEY",
    "DATABASE_URL",
    "VALKEY_URL",
}
_ACTIVE = re.compile(r"^([A-Z][A-Z0-9_]+)=(.*)$")
_COMMENTED = re.compile(r"^# ([A-Z][A-Z0-9_]+)=")


def _documented() -> tuple[dict[str, str], set[str]]:
    """(active variables with their values, commented-out variable names)."""
    active: dict[str, str] = {}
    commented: set[str] = set()
    for line in EXAMPLE.read_text().splitlines():
        if match := _ACTIVE.match(line):
            active[match.group(1)] = match.group(2)
        elif match := _COMMENTED.match(line):
            commented.add(match.group(1))
    return active, commented


def _setting_names() -> set[str]:
    return {str(field.validation_alias) for field in Settings.model_fields.values()}


class TestExampleDescribesTheSettings:
    def test_every_setting_is_documented(self):
        active, commented = _documented()

        assert _setting_names() - (set(active) | commented) == set()

    def test_nothing_documented_is_unknown_to_the_app(self):
        active, commented = _documented()

        assert (set(active) | commented) - _setting_names() == set()

    def test_the_variables_needed_to_start_are_not_commented_out(self):
        active, _ = _documented()

        assert set(active) >= REQUIRED_AT_START


class TestExampleStartsTheApp:
    @pytest.fixture
    def example_environment(self, monkeypatch):
        active, _ = _documented()
        for name in _setting_names():
            monkeypatch.delenv(name, raising=False)
        for name, value in active.items():
            monkeypatch.setenv(name, value)
        return active

    def test_the_placeholder_secret_key_is_refused(self, example_environment):
        with pytest.raises(SystemExit):
            Settings()

    def test_it_starts_once_the_placeholders_are_replaced(
        self, example_environment, monkeypatch
    ):
        monkeypatch.setenv("SECRET_KEY", secrets.token_urlsafe(48))

        settings = Settings()

        assert settings.app_environment == "development"

    def test_the_database_url_names_an_installed_driver(self, example_environment):
        engine = create_engine(example_environment["DATABASE_URL"])

        assert engine.dialect.name == "postgresql"


class TestExampleRules:
    def test_comments_are_english_only(self):
        assert not re.search(r"^#\s*DE:", EXAMPLE.read_text(), flags=re.MULTILINE)

    def test_example_hosts_are_reserved_names(self):
        allowed = ("localhost", "127.0.0.1", "example.org")
        text = EXAMPLE.read_text()
        hosts = re.findall(r"(?:https?|redis)://(?:[^@/\s]+@)?([^/:,\s]+)", text)
        hosts += re.findall(r"@([a-z0-9.-]+\.[a-z]{2,})\b", text)

        assert [h for h in hosts if not h.endswith(allowed)] == []

    def test_host_settings_name_reserved_hosts(self):
        values = re.findall(
            r"^#?\s*[A-Z0-9_]*HOST=(\S+)$", EXAMPLE.read_text(), flags=re.MULTILINE
        )

        assert values
        assert [v for v in values if v not in ("localhost", "127.0.0.1")] == []

    def test_example_sender_addresses_use_a_reserved_domain(self):
        senders = re.findall(
            r"^#?\s*[A-Z0-9_]*(?:FROM_EMAIL|_EMAIL)=(\S+)$",
            EXAMPLE.read_text(),
            flags=re.MULTILINE,
        )

        assert senders
        assert [a for a in senders if not a.endswith("@example.org")] == []

    def test_no_member_identifier_is_published(self):
        assert not re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            EXAMPLE.read_text(),
        )
