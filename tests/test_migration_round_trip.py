"""The whole migration chain runs down to the base and up again, and the schema
that comes out is the one that went in.

Rolling back shortly after a deploy, or resetting a development database with
`alembic downgrade base`, only works while every revision can be undone on a
database that later revisions have already changed. The suite never ran a
downgrade, so two breaks stayed unseen for a long time.

The chain runs in a database of its own on the same server, through the
`alembic` command line (a separate process), so nothing of the test session's
schema or settings is touched."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from tests.conftest import TEST_DATABASE_URL

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 300

_SCHEMA_QUERIES = {
    "columns": (
        "SELECT table_name, column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2"
    ),
    "indexes": (
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' ORDER BY 1"
    ),
    "constraints": (
        "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
        "FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2"
    ),
    "triggers": (
        "SELECT tgrelid::regclass::text, tgname, pg_get_triggerdef(oid) "
        "FROM pg_trigger WHERE NOT tgisinternal ORDER BY 1, 2"
    ),
    "functions": (
        "SELECT proname, pg_get_functiondef(oid) FROM pg_proc "
        "WHERE pronamespace = 'public'::regnamespace ORDER BY 1"
    ),
}


def _url(database: str) -> URL:
    return make_url(TEST_DATABASE_URL).set(database=database)


def _alembic(database_url: URL, *arguments: str) -> subprocess.CompletedProcess[str]:
    env = os.environ | {
        "DATABASE_URL": database_url.render_as_string(hide_password=False)
    }
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", *arguments],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


def _signature(database_url: URL) -> dict[str, list[tuple[object, ...]]]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return {
                name: [tuple(row) for row in connection.execute(text(query))]
                for name, query in _SCHEMA_QUERIES.items()
            }
    finally:
        engine.dispose()


def _run_ok(database_url: URL, *arguments: str) -> None:
    result = _alembic(database_url, *arguments)
    assert result.returncode == 0, (
        f"alembic {' '.join(arguments)} failed:\n{result.stderr[-3000:]}"
    )


@pytest.fixture
def scratch_database():
    name = f"vb_round_trip_{uuid.uuid4().hex[:12]}"
    admin = create_engine(_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield _url(name)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.mark.slow
def test_the_chain_downgrades_to_the_base_and_comes_back_unchanged(scratch_database):
    _run_ok(scratch_database, "upgrade", "head")
    built = _signature(scratch_database)
    assert built["columns"], "the upgrade created no tables"

    _run_ok(scratch_database, "downgrade", "base")
    _run_ok(scratch_database, "upgrade", "head")

    again = _signature(scratch_database)
    assert {name: rows == built[name] for name, rows in again.items()} == dict.fromkeys(
        built, True
    )
