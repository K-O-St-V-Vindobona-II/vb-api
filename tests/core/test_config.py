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
from typing import ClassVar

import pytest
from pydantic import ValidationError

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


def test_missing_valkey_url_aborts_with_fatal_message() -> None:
    result = _run(
        "from app.core.config import get_settings; get_settings()",
        {"APP_ENVIRONMENT": "test", "CORS_ORIGINS": "https://a.example.com"},
        unset=["VALKEY_URL"],
    )

    assert result.returncode == 1
    assert "VALKEY_URL is not set" in result.stderr


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


class TestDownsyncLight:
    """Tier 3 - off unless a non-production stage asks for the light downsync."""

    def test_defaults_to_off_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("DOWNSYNC_LIGHT", raising=False)
        assert get_settings().downsync_light is False

    def test_true_switches_it_on(self, monkeypatch) -> None:
        monkeypatch.setenv("DOWNSYNC_LIGHT", "true")
        assert get_settings().downsync_light is True

    def test_false_keeps_it_off(self, monkeypatch) -> None:
        monkeypatch.setenv("DOWNSYNC_LIGHT", "false")
        assert get_settings().downsync_light is False


class TestAppTimezone:
    """Tier 3 (timezone) — optional with a sane default, no boot-time
    validation (an invalid IANA zone name only surfaces where it's
    actually resolved via zoneinfo.ZoneInfo, e.g. app/core/scheduler.py)."""

    def test_defaults_to_europe_vienna_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("APP_TIMEZONE", raising=False)
        assert get_settings().app_timezone == "Europe/Vienna"

    def test_env_var_overrides_default(self, monkeypatch) -> None:
        monkeypatch.setenv("APP_TIMEZONE", "America/New_York")
        assert get_settings().app_timezone == "America/New_York"


def _iban_with_valid_checksum(country: str, bban: str) -> str:
    """An IBAN of the given country and basic account number whose check
    digits are computed, so only its length decides whether it is accepted."""
    digits = "".join(str(int(char, 36)) for char in bban + country) + "00"
    return f"{country}{98 - int(digits) % 97:02d}{bban}"


class TestAktivitasPaymentSettings:
    """Tier 2 (feature-required): unset is allowed at start and refused where
    the payment page needs the value; a value that is set must be well formed."""

    NAMES = (
        "AKTIVITAS_ACCOUNT_HOLDER",
        "AKTIVITAS_IBAN",
        "AKTIVITAS_BIC",
        "AKTIVITAS_FEE_TEXT",
    )

    def test_all_four_are_unset_by_default(self, monkeypatch) -> None:
        for name in self.NAMES:
            monkeypatch.delenv(name, raising=False)

        settings = get_settings()

        assert settings.aktivitas_account_holder is None
        assert settings.aktivitas_iban is None
        assert settings.aktivitas_bic is None
        assert settings.aktivitas_fee_text is None

    def test_the_values_are_read_as_given(self, monkeypatch) -> None:
        monkeypatch.setenv("AKTIVITAS_ACCOUNT_HOLDER", "Verein Muster")
        monkeypatch.setenv("AKTIVITAS_IBAN", "AT61 1904 3002 3457 3201")
        monkeypatch.setenv("AKTIVITAS_BIC", "BKAUATWW")
        monkeypatch.setenv("AKTIVITAS_FEE_TEXT", "Beitrag: EUR 30,- / Semester")

        settings = get_settings()

        assert settings.aktivitas_account_holder == "Verein Muster"
        assert settings.aktivitas_iban == "AT61 1904 3002 3457 3201"
        assert settings.aktivitas_bic == "BKAUATWW"
        assert settings.aktivitas_fee_text == "Beitrag: EUR 30,- / Semester"

    @pytest.mark.parametrize(
        "iban",
        [
            "AT611904300234573201",
            "AT61 1904 3002 3457 3201",
            "DE89370400440532013000",
            "GB82WEST12345698765432",
        ],
    )
    def test_a_valid_iban_is_accepted(self, monkeypatch, iban) -> None:
        monkeypatch.setenv("AKTIVITAS_IBAN", iban)

        assert get_settings().aktivitas_iban == iban

    @pytest.mark.parametrize(
        "iban",
        [
            "AT611904300234573202",
            "AT62 1904 3002 3457 3201",
            "at611904300234573201",
            "AT61",
            "AT61-1904-3002-3457-3201",
            "",
            "   ",
        ],
    )
    def test_a_malformed_iban_or_wrong_checksum_is_refused(
        self, monkeypatch, iban
    ) -> None:
        monkeypatch.setenv("AKTIVITAS_IBAN", iban)

        with pytest.raises(ValidationError, match="AKTIVITAS_IBAN"):
            get_settings()

    def test_surrounding_whitespace_is_dropped(self, monkeypatch) -> None:
        monkeypatch.setenv("AKTIVITAS_ACCOUNT_HOLDER", "  Verein Muster ")
        monkeypatch.setenv("AKTIVITAS_IBAN", " AT61 1904 3002 3457 3201 ")
        monkeypatch.setenv("AKTIVITAS_BIC", "BKAUATWW ")
        monkeypatch.setenv("AKTIVITAS_FEE_TEXT", " Beitrag ")

        settings = get_settings()

        assert settings.aktivitas_account_holder == "Verein Muster"
        assert settings.aktivitas_iban == "AT61 1904 3002 3457 3201"
        assert settings.aktivitas_bic == "BKAUATWW"
        assert settings.aktivitas_fee_text == "Beitrag"

    @pytest.mark.parametrize("bban_length", [11, 30])
    def test_the_shortest_and_longest_iban_are_accepted(
        self, monkeypatch, bban_length
    ) -> None:
        iban = _iban_with_valid_checksum("XX", "1" * bban_length)
        monkeypatch.setenv("AKTIVITAS_IBAN", iban)

        assert get_settings().aktivitas_iban == iban

    @pytest.mark.parametrize("bban_length", [10, 31])
    def test_an_iban_outside_the_length_range_is_refused(
        self, monkeypatch, bban_length
    ) -> None:
        monkeypatch.setenv(
            "AKTIVITAS_IBAN", _iban_with_valid_checksum("XX", "1" * bban_length)
        )

        with pytest.raises(ValidationError, match="AKTIVITAS_IBAN"):
            get_settings()

    @pytest.mark.parametrize("bic", ["GIBAATWWXXX", "GIBAATWW", "BKAUATWW123"])
    def test_a_valid_bic_is_accepted(self, monkeypatch, bic) -> None:
        monkeypatch.setenv("AKTIVITAS_BIC", bic)

        assert get_settings().aktivitas_bic == bic

    @pytest.mark.parametrize(
        "bic",
        [
            "gibaatwwxxx",
            "GIBAAT",
            "GIBAATWWXX",
            "GIBAATWWXXXX",
            "GIBAATWWXXXXXX",
            "GIBA-ATWW",
            "",
        ],
    )
    def test_a_malformed_bic_is_refused(self, monkeypatch, bic) -> None:
        monkeypatch.setenv("AKTIVITAS_BIC", bic)

        with pytest.raises(ValidationError, match="AKTIVITAS_BIC"):
            get_settings()

    @pytest.mark.parametrize("name", ["AKTIVITAS_ACCOUNT_HOLDER", "AKTIVITAS_FEE_TEXT"])
    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_text_is_refused(self, monkeypatch, name, blank) -> None:
        monkeypatch.setenv(name, blank)

        with pytest.raises(ValidationError, match=name):
            get_settings()


class TestPlaceholderSecretKey:
    @pytest.mark.parametrize(
        "key",
        [
            "change-me-to-a-long-random-string-at-least-32-chars",
            "CHANGE-ME-to-a-long-random-string-at-least-32-chars",
            "prefix-changeme-0123456789abcdef0123456789",
            "prefix-replace-me-0123456789abcdef01234567",
            "your-secret-key-0123456789abcdef0123456789",
        ],
    )
    def test_a_key_from_an_example_file_aborts(self, monkeypatch, capsys, key) -> None:
        monkeypatch.setenv("SECRET_KEY", key)

        with pytest.raises(SystemExit) as info:
            get_settings()

        assert info.value.code == 1
        assert "placeholder" in capsys.readouterr().err

    def test_a_random_key_is_accepted(self, monkeypatch) -> None:
        monkeypatch.setenv("SECRET_KEY", "k3J9xQ2vT7mZp0LwR8aYb5NcD1eHs6UfG4")

        assert get_settings().secret_key == "k3J9xQ2vT7mZp0LwR8aYb5NcD1eHs6UfG4"


class TestCorsOriginShape:
    @pytest.mark.parametrize(
        "origins",
        [
            "*",
            "https://*.example.com",
            "https://example.com/",
            "https://example.com/app",
            "example.com",
            "https://a.example.com, https://b.example.com/",
            "ftp://example.com",
        ],
    )
    def test_a_malformed_origin_aborts(self, monkeypatch, capsys, origins) -> None:
        monkeypatch.setenv("CORS_ORIGINS", origins)

        with pytest.raises(SystemExit) as info:
            get_settings()

        assert info.value.code == 1
        assert "malformed origins" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "origins",
        [
            "https://example.com",
            "http://localhost:20001",
            "https://a.example.com, http://localhost:5173",
        ],
    )
    def test_scheme_host_and_port_are_accepted(self, monkeypatch, origins) -> None:
        monkeypatch.setenv("CORS_ORIGINS", origins)

        assert get_settings().cors_origins_list


class TestNumericBounds:
    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("SESSION_LIFETIME_MINUTES", "0"),
            ("SESSION_LIFETIME_MINUTES", "1441"),
            ("SESSION_IDLE_TIMEOUT_MINUTES", "0"),
            ("SESSION_IDLE_TIMEOUT_MINUTES", "-5"),
            ("SESSION_IDLE_TIMEOUT_MINUTES", "10081"),
            ("REFRESH_TOKEN_LIFETIME_DAYS", "0"),
            ("REFRESH_TOKEN_LIFETIME_DAYS", "91"),
            ("PASSWORD_MIN_LENGTH", "3"),
            ("PASSWORD_MIN_LENGTH", "7"),
            ("PASSWORD_MIN_LENGTH", "73"),
            ("PASSWORD_MIN_LENGTH", "100"),
            ("BCRYPT_ROUNDS", "3"),
            ("BCRYPT_ROUNDS", "17"),
            ("SMTP_TIMEOUT_SECONDS", "0"),
            ("SMTP_TIMEOUT_SECONDS", "301"),
            ("SMTP_PORT", "0"),
            ("SMTP_PORT", "65536"),
            ("S3_PRESIGNED_EXPIRY", "0"),
            ("S3_PRESIGNED_EXPIRY", "604801"),
            ("BACKUP_HOUR", "24"),
            ("BACKUP_HOUR", "-1"),
            ("BACKUP_RETENTION_DAYS", "0"),
            ("TRACKING_RETENTION_MONTHS", "0"),
        ],
    )
    def test_a_value_outside_its_range_is_refused(
        self, monkeypatch, name, value
    ) -> None:
        monkeypatch.setenv(name, value)

        with pytest.raises(ValidationError, match=name):
            get_settings()

    @pytest.mark.parametrize(
        ("name", "value", "attribute"),
        [
            ("SESSION_LIFETIME_MINUTES", "1", "session_lifetime_minutes"),
            ("SESSION_LIFETIME_MINUTES", "1440", "session_lifetime_minutes"),
            ("SESSION_IDLE_TIMEOUT_MINUTES", "1", "session_idle_timeout_minutes"),
            ("SESSION_IDLE_TIMEOUT_MINUTES", "10080", "session_idle_timeout_minutes"),
            ("REFRESH_TOKEN_LIFETIME_DAYS", "1", "refresh_token_lifetime_days"),
            ("REFRESH_TOKEN_LIFETIME_DAYS", "90", "refresh_token_lifetime_days"),
            ("PASSWORD_MIN_LENGTH", "8", "password_min_length"),
            ("PASSWORD_MIN_LENGTH", "72", "password_min_length"),
            ("BCRYPT_ROUNDS", "4", "bcrypt_rounds"),
            ("BCRYPT_ROUNDS", "16", "bcrypt_rounds"),
            ("SMTP_TIMEOUT_SECONDS", "1", "smtp_timeout_seconds"),
            ("SMTP_TIMEOUT_SECONDS", "300", "smtp_timeout_seconds"),
            ("SMTP_PORT", "1", "smtp_port"),
            ("SMTP_PORT", "65535", "smtp_port"),
            ("S3_PRESIGNED_EXPIRY", "1", "s3_presigned_expiry"),
            ("BACKUP_RETENTION_DAYS", "1", "backup_retention_days"),
            ("TRACKING_RETENTION_MONTHS", "1", "tracking_retention_months"),
            ("S3_PRESIGNED_EXPIRY", "604800", "s3_presigned_expiry"),
            ("BACKUP_HOUR", "0", "backup_hour"),
            ("BACKUP_HOUR", "23", "backup_hour"),
        ],
    )
    def test_the_edges_of_each_range_are_accepted(
        self, monkeypatch, name, value, attribute
    ) -> None:
        monkeypatch.setenv(name, value)

        assert getattr(get_settings(), attribute) == int(value)

    def test_the_defaults_lie_inside_the_ranges(self, monkeypatch) -> None:
        for name in (
            "SESSION_LIFETIME_MINUTES",
            "SESSION_IDLE_TIMEOUT_MINUTES",
            "REFRESH_TOKEN_LIFETIME_DAYS",
            "PASSWORD_MIN_LENGTH",
            "S3_PRESIGNED_EXPIRY",
            "BACKUP_HOUR",
            "BACKUP_RETENTION_DAYS",
            "TRACKING_RETENTION_MONTHS",
        ):
            monkeypatch.delenv(name, raising=False)

        settings = get_settings()

        assert (settings.session_lifetime_minutes, settings.password_min_length) == (
            15,
            8,
        )


class TestSecretsStayOutOfRepr:
    SECRETS: ClassVar[dict[str, str]] = {
        "SECRET_KEY": "sk-0123456789abcdef0123456789abcdef-visible-in-repr",
        "DATABASE_URL": "postgresql://user:db-password-visible@host/db",
        "VALKEY_URL": "redis://:valkey-password-visible@host:6379/0",
        "S3_ACCESS_KEY": "s3-access-key-visible",
        "S3_SECRET_KEY": "s3-secret-key-visible",
        "AWS_ACCESS_KEY_ID": "aws-access-key-visible",
        "AWS_SECRET_ACCESS_KEY": "aws-secret-key-visible",
        "SMTP_PASSWORD": "smtp-password-visible",
    }

    @pytest.mark.parametrize("render", [repr, str])
    def test_no_secret_value_is_printed(self, monkeypatch, render) -> None:
        for name, value in self.SECRETS.items():
            monkeypatch.setenv(name, value)

        text = render(get_settings())

        assert [v for v in self.SECRETS.values() if v in text] == []

    def test_the_values_are_still_available_to_the_code(self, monkeypatch) -> None:
        for name, value in self.SECRETS.items():
            monkeypatch.setenv(name, value)

        settings = get_settings()

        assert settings.smtp_password == self.SECRETS["SMTP_PASSWORD"]
        assert settings.database_url == self.SECRETS["DATABASE_URL"]

    def test_non_secret_settings_are_still_printed(self, monkeypatch) -> None:
        monkeypatch.setenv("S3_BUCKET", "some-visible-bucket")

        assert "some-visible-bucket" in repr(get_settings())
