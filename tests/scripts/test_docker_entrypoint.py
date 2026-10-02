"""docker-entrypoint.sh: migrate first, refuse to start on a real failure, but
start an older image against a database that is ahead of it (a rollback)."""

import os
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "docker-entrypoint.sh"

UNKNOWN_REVISION = "FAILED: Can't locate revision identified by '9e5abc37f6b8'"


def _fake_alembic(bin_dir: Path, *, output: str, exit_code: int) -> Path:
    """Put an `alembic` on PATH that records its call and answers as told."""
    called = bin_dir / "alembic.called"
    script = bin_dir / "alembic"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{called}"\n'
        f"cat <<'EOF'\n{output}\nEOF\n"
        f"exit {exit_code}\n"
    )
    script.chmod(0o755)
    return called


def _run(
    bin_dir: Path, *command: str, skip_migrations: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    if skip_migrations is not None:
        env["SKIP_MIGRATIONS"] = skip_migrations
    # Fixed script under test and literal commands from this file, never
    # external input: S603/S607 do not apply.
    return subprocess.run(  # noqa: S603
        ["sh", str(ENTRYPOINT), *command],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class TestEntrypoint:
    def test_migrates_then_starts_the_command(self, tmp_path):
        called = _fake_alembic(tmp_path, output="INFO all good", exit_code=0)

        result = _run(tmp_path, "echo", "APP STARTED")

        assert result.returncode == 0
        assert called.read_text().strip() == "upgrade head"
        assert "Running database migrations..." in result.stdout
        assert result.stdout.rstrip().endswith("APP STARTED")

    def test_a_failed_migration_stops_the_container(self, tmp_path):
        _fake_alembic(tmp_path, output="FAILED: connection refused", exit_code=1)

        result = _run(tmp_path, "echo", "APP STARTED")

        assert result.returncode == 1
        assert "APP STARTED" not in result.stdout
        assert "connection refused" in result.stdout

    def test_a_database_ahead_of_the_image_still_starts(self, tmp_path):
        _fake_alembic(tmp_path, output=UNKNOWN_REVISION, exit_code=255)

        result = _run(tmp_path, "echo", "APP STARTED")

        assert result.returncode == 0
        assert "APP STARTED" in result.stdout
        assert "database is ahead of this image" in result.stderr

    def test_the_ahead_case_is_reported_not_swallowed(self, tmp_path):
        _fake_alembic(tmp_path, output=UNKNOWN_REVISION, exit_code=255)

        result = _run(tmp_path, "echo", "APP STARTED")

        assert "Can't locate revision" in result.stdout

    def test_skip_migrations_true_does_not_call_alembic(self, tmp_path):
        called = _fake_alembic(tmp_path, output="", exit_code=0)

        result = _run(tmp_path, "echo", "WORKER STARTED", skip_migrations="true")

        assert result.returncode == 0
        assert not called.exists()
        assert "WORKER STARTED" in result.stdout

    @pytest.mark.parametrize("value", ["false", "1", "TRUE", ""])
    def test_any_other_value_still_migrates(self, tmp_path, value):
        called = _fake_alembic(tmp_path, output="", exit_code=0)

        _run(tmp_path, "true", skip_migrations=value)

        assert called.exists()

    def test_the_exit_code_of_the_command_is_passed_on(self, tmp_path):
        _fake_alembic(tmp_path, output="", exit_code=0)

        result = _run(tmp_path, "sh", "-c", "exit 7")

        assert result.returncode == 7
