"""Regression tests for app/core/config.py's fail-fast env parsing.

config.py validates its env vars at import time, and conftest.py imports it
once per test session (transitively via main.py) — an in-process
importlib.reload() would leak state into every other test. These tests
therefore spawn a fresh subprocess per case, mirroring how the real app
process starts.
"""

import os
import subprocess
import sys
from pathlib import Path

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


def test_missing_cors_origins_aborts_with_fatal_message() -> None:
    result = _run(
        "import app.core.config",
        {"APP_ENVIRONMENT": "test"},
        unset=["CORS_ORIGINS"],
    )

    assert result.returncode == 1
    assert "CORS_ORIGINS is not set" in result.stderr


def test_blank_cors_origins_aborts_with_fatal_message() -> None:
    result = _run(
        "import app.core.config",
        {"APP_ENVIRONMENT": "test", "CORS_ORIGINS": " , , "},
    )

    assert result.returncode == 1
    assert "CORS_ORIGINS is set but contains no valid origins" in result.stderr


def test_valid_cors_origins_are_split_and_stripped() -> None:
    result = _run(
        "import app.core.config as c; print(c.CORS_ORIGINS)",
        {
            "APP_ENVIRONMENT": "test",
            "CORS_ORIGINS": " https://a.example.com ,https://b.example.com ",
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['https://a.example.com', 'https://b.example.com']"
