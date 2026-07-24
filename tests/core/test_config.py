"""Regression tests for app/core/config.py's fail-fast env parsing.

Settings validation now runs inside get_settings() (called explicitly),
not merely on module import — get_settings() is lru_cache'd so that
in-process tests can clear the cache and re-validate under different env
vars (see tests/conftest.py's _reset_settings_cache fixture). These tests
still spawn a fresh subprocess per case: they assert on process exit code
and stderr, mirroring how the real app process starts (main.py calls
get_settings() synchronously at import time).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import get_settings, require_setting

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run(code: str, overrides: dict[str, str], unset: list[str] | None = None):
    env = {**os.environ, **overrides}
    for key in unset or []:
        env.pop(key, None)
    # code is always a hardcoded literal from this file, never external
    # input — S603 doesn't apply here.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_missing_app_environment_aborts_with_fatal_message() -> None:
    result = _run(
        "from app.core.config import get_settings; get_settings()",
        {"CORS_ORIGINS": "https://a.example.com"},
        unset=["APP_ENVIRONMENT"],
    )

    assert result.returncode == 1
    assert "APP_ENVIRONMENT is not set" in result.stderr


def test_invalid_app_environment_aborts_with_fatal_message() -> None:
    result = _run(
        "from app.core.config import get_settings; get_settings()",
        {"APP_ENVIRONMENT": "staging", "CORS_ORIGINS": "https://a.example.com"},
    )

    assert result.returncode == 1
    assert "APP_ENVIRONMENT='staging' is invalid" in result.stderr


def test_missing_cors_origins_aborts_with_fatal_message() -> None:
    result = _run(
        "from app.core.config import get_settings; get_settings()",
        {"APP_ENVIRONMENT": "test"},
        unset=["CORS_ORIGINS"],
    )

    assert result.returncode == 1
    assert "CORS_ORIGINS is not set" in result.stderr


def test_blank_cors_origins_aborts_with_fatal_message() -> None:
    result = _run(
        "from app.core.config import get_settings; get_settings()",
        {"APP_ENVIRONMENT": "test", "CORS_ORIGINS": " , , "},
    )

    assert result.returncode == 1
    assert "CORS_ORIGINS is set but contains no valid origins" in result.stderr


def test_missing_secret_key_aborts_with_fatal_message() -> None:
    result = _run(
        "from app.core.config import get_settings; get_settings()",
        {"APP_ENVIRONMENT": "test", "CORS_ORIGINS": "https://a.example.com"},
        unset=["SECRET_KEY"],
    )

    assert result.returncode == 1
    assert "SECRET_KEY is not set" in result.stderr


def test_short_secret_key_aborts_with_fatal_message() -> None:
    result = _run(
        "from app.core.config import get_settings; get_settings()",
        {
            "APP_ENVIRONMENT": "test",
            "CORS_ORIGINS": "https://a.example.com",
            "SECRET_KEY": "too-short",
        },
    )

    assert result.returncode == 1
    assert "SECRET_KEY must be at least 32 characters long" in result.stderr


def test_missing_database_url_aborts_with_fatal_message() -> None:
    result = _run(
        "from app.core.config import get_settings; get_settings()",
        {"APP_ENVIRONMENT": "test", "CORS_ORIGINS": "https://a.example.com"},
        unset=["DATABASE_URL"],
    )

    assert result.returncode == 1
    assert "DATABASE_URL is not set" in result.stderr


def test_valid_cors_origins_are_split_and_stripped() -> None:
    result = _run(
        "from app.core.config import get_settings; "
        "print(get_settings().cors_origins_list)",
        {
            "APP_ENVIRONMENT": "test",
            "CORS_ORIGINS": " https://a.example.com ,https://b.example.com ",
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['https://a.example.com', 'https://b.example.com']"


class TestRequireSetting:
    """Tier 2 ('feature-required, checked at first use') helper."""

    def test_returns_value_when_present(self) -> None:
        assert require_setting("configured", "SOME_VAR") == "configured"

    def test_raises_runtime_error_naming_the_env_var_when_missing(self) -> None:
        with pytest.raises(RuntimeError, match="SOME_VAR is not set"):
            require_setting(None, "SOME_VAR")


class TestBackupEnabledParsing:
    """Historical convention: anything but the literal string "false"
    (case-insensitive) means enabled — deliberately looser than pydantic's
    default strict bool parsing (see Settings._parse_backup_enabled)."""

    def test_defaults_to_enabled_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("BACKUP_ENABLED", raising=False)
        assert get_settings().backup_enabled is True

    def test_false_case_insensitive_disables(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKUP_ENABLED", "FALSE")
        assert get_settings().backup_enabled is False

    def test_arbitrary_string_is_treated_as_enabled(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKUP_ENABLED", "yes-please")
        assert get_settings().backup_enabled is True
