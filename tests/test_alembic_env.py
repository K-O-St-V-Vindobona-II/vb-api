"""alembic/env.py: migrations must run whatever the credentials look like and
must not silence the caller's logging."""

import configparser
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.db.migration_lock import MIGRATION_LOCK_KEY

ROOT = Path(__file__).resolve().parents[1]
_REQUIRED_SETTINGS = {
    "SECRET_KEY": "k" * 40,
    "VALKEY_URL": "redis://localhost:6379/0",
    "CORS_ORIGINS": "http://localhost:20001",
    "APP_ENVIRONMENT": "test",
}
# A revision made of plain DDL: it can be rendered as SQL without a database.
DDL_ONLY_REVISION_RANGE = "9e5abc37f6b8:b7e4a2c91d35"


_SOCIAL_SORT = "ALTER TABLE public_site_social_links ALTER COLUMN sort_order"
_LOG_TIME = "ALTER TABLE contacts_logs ALTER COLUMN modified_at"
# (statement that makes the database differ from the models, statement that undoes it)
_SCHEMA_DRIFTS = [
    (f"{_SOCIAL_SORT} TYPE bigint", f"{_SOCIAL_SORT} TYPE integer"),
    (f"{_LOG_TIME} SET DEFAULT '2000-01-01'", f"{_LOG_TIME} SET DEFAULT now()"),
]
_DRIFT_IDS = ["column type", "server default"]


def _run(command: list[str], database_url: str) -> subprocess.CompletedProcess[str]:
    # Fixed interpreter and literal arguments from this file: S603 does not apply.
    return subprocess.run(  # noqa: S603
        command,
        cwd=ROOT,
        env={**os.environ, **_REQUIRED_SETTINGS, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )


def _failure(result: subprocess.CompletedProcess[str], url: str) -> str:
    """The error output of a run, without the database URL (and with it the
    password) that tracebacks and messages of the tool may contain."""
    return result.stderr.replace(url, "<database url>")


def _with_percent_encoded_option(url: str) -> str:
    """The same URL plus a connection option that contains a percent sign, as
    a credential such as "p%40ss" would (SQLAlchemy decodes both alike)."""
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}application_name=vb%20alembic%20test"


class TestEnv:
    def test_a_percent_encoded_url_still_reaches_the_database(self):
        url = _with_percent_encoded_option(os.environ["DATABASE_URL"])
        assert "%" in url

        result = _run([sys.executable, "-m", "alembic", "current"], url)

        exit_code = result.returncode
        assert exit_code == 0, _failure(result, url)
        stdout = result.stdout
        assert "(head)" in stdout

    def test_the_offline_mode_accepts_a_percent_encoded_url_too(self):
        url = _with_percent_encoded_option(os.environ["DATABASE_URL"])

        result = _run(
            [
                sys.executable,
                "-m",
                "alembic",
                "upgrade",
                DDL_ONLY_REVISION_RANGE,
                "--sql",
            ],
            url,
        )

        exit_code = result.returncode
        assert exit_code == 0, _failure(result, url)
        stdout = result.stdout
        assert "ALTER TABLE members ALTER COLUMN gruender SET NOT NULL" in stdout

    def test_running_migrations_keeps_the_callers_loggers_enabled(self):
        script = (
            "import logging\n"
            "from alembic.config import CommandLine\n"
            "logging.getLogger('app.services.example')\n"
            "CommandLine(prog='alembic').main(argv=['current'])\n"
            "print('DISABLED' if logging.getLogger('app.services.example').disabled"
            " else 'ENABLED')\n"
        )

        url = os.environ["DATABASE_URL"]
        result = _run([sys.executable, "-c", script], url)

        exit_code = result.returncode
        assert exit_code == 0, _failure(result, url)
        stdout = result.stdout
        assert "ENABLED" in stdout

    def test_autogenerate_proposes_nothing_for_a_migrated_database(self):
        # Without the filter for indexes that exist only in migrations, and
        # with the comparison of types and defaults switched off, the tool
        # would propose to drop every search index (or miss a changed default).
        url = os.environ["DATABASE_URL"]

        result = _run([sys.executable, "-m", "alembic", "check"], url)

        exit_code = result.returncode
        assert exit_code == 0, _failure(result, url)
        stdout = result.stdout
        assert "No new upgrade operations detected" in stdout

    @pytest.mark.parametrize(("drift", "undo"), _SCHEMA_DRIFTS, ids=_DRIFT_IDS)
    def test_autogenerate_notices_a_changed_type_or_default(self, drift, undo):
        url = os.environ["DATABASE_URL"]
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(text(drift))

            result = _run([sys.executable, "-m", "alembic", "check"], url)
        finally:
            with engine.begin() as connection:
                connection.execute(text(undo))
            engine.dispose()

        assert result.returncode != 0, _failure(result, url)
        assert "New upgrade operations detected" in result.stderr + result.stdout

    def test_env_py_does_not_read_the_environment_itself(self):
        source = (ROOT / "alembic" / "env.py").read_text()

        assert "os.environ" not in source
        assert "getenv" not in source
        assert "get_settings" in source

    def test_the_url_is_not_kept_in_the_ini_file_whose_parser_interpolates(self):
        parser = configparser.RawConfigParser()
        parser.read(ROOT / "alembic.ini")

        assert not parser.has_option("alembic", "sqlalchemy.url")

    def test_the_ban_on_reading_the_environment_applies_to_env_py(self):
        ignores = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["ruff"][
            "lint"
        ]["per-file-ignores"]

        assert "TID251" not in ignores["alembic/**"]


class TestMigrationLock:
    def test_a_second_migration_waits_for_the_first(self):
        url = os.environ["DATABASE_URL"]
        engine = create_engine(url)
        with engine.connect() as holder:
            holder.execute(
                text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
            )
            second = subprocess.Popen(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=ROOT,
                env={**os.environ, **_REQUIRED_SETTINGS, "DATABASE_URL": url},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                with pytest.raises(subprocess.TimeoutExpired):
                    second.wait(timeout=4)
            finally:
                holder.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": MIGRATION_LOCK_KEY},
                )
            _, stderr = second.communicate(timeout=60)

            # The run held the lock only for its own transaction.
            free = holder.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
            ).scalar()
            holder.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY}
            )

        engine.dispose()
        returncode = second.returncode
        assert returncode == 0, stderr.replace(url, "<database url>")
        assert free is True

    def test_env_py_takes_the_lock_before_running_migrations(self):
        source = (ROOT / "alembic" / "env.py").read_text()
        online = source.index("def run_migrations_online")

        assert "pg_advisory_xact_lock" in source[online:]
        assert source.index("pg_advisory_xact_lock", online) < source.index(
            "context.run_migrations()", online
        )

    def test_the_lock_key_is_a_valid_bigint(self):
        assert 0 < MIGRATION_LOCK_KEY < 2**63
