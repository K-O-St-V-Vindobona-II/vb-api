"""Shared pytest fixtures.

The test suite runs exclusively against a real PostgreSQL database — see
scripts/README.md for the local/CI test-database setup convention. The
schema is built once per test session via the actual Alembic migrations
(not Base.metadata.create_all()), so the tests also catch model/migration
drift. Each test runs inside an outer transaction (with a SAVEPOINT for the
ORM session) that is always rolled back afterward for isolation.
"""

import os
from unittest.mock import patch

os.environ["APP_ENVIRONMENT"] = "test"
os.environ["CORS_ORIGINS"] = "http://localhost:20001,http://127.0.0.1:20001"

_ALLOWED_TEST_DBS = {"vb_test", "test"}
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL"
)
if not TEST_DATABASE_URL:
    msg = (
        "TEST_DATABASE_URL (or DATABASE_URL) is not set. Point it at a "
        "dedicated PostgreSQL test database, e.g. "
        "postgresql+psycopg2://vb:<pw>@localhost:5432/vb_test — see "
        "scripts/README.md."
    )
    raise RuntimeError(msg)

_dbname = TEST_DATABASE_URL.rsplit("/", 1)[-1].split("?")[0]
if _dbname not in _ALLOWED_TEST_DBS:
    msg = (
        f"Refusing to run tests against non-test database {_dbname!r}. "
        f"Allowed test database names: {sorted(_ALLOWED_TEST_DBS)}. The "
        "test session drops and rebuilds the 'public' schema — pointing "
        "this at a real dev/prod database would destroy it."
    )
    raise RuntimeError(msg)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # consulted by alembic/env.py

import bcrypt
import boto3
import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.core import storage as storage_module
from app.core.storage import StorageClient, get_storage
from app.db.database import get_db
from main import app

engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
if engine.dialect.name != "postgresql":
    msg = (
        f"Test suite requires PostgreSQL, got dialect {engine.dialect.name!r}. "
        "SQLite fallbacks are not supported — see scripts/README.md."
    )
    raise RuntimeError(msg)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Plain module-level holder, not a ContextVar: TestClient runs the ASGI app
# in a separate worker thread via an anyio blocking portal, and ContextVar
# values set in the test thread don't reliably propagate there. The suite
# runs fully serially (no pytest-xdist), so a single module global is safe.
_active_session: Session | None = None


@pytest.fixture(scope="session", autouse=True)
def _create_schema() -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def _db_transaction():
    global _active_session
    connection = engine.connect()
    trans = connection.begin()
    session = TestingSessionLocal(
        bind=connection, join_transaction_mode="create_savepoint"
    )
    _active_session = session
    try:
        yield session
    finally:
        _active_session = None
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture
def db_session(_db_transaction: Session) -> Session:
    return _db_transaction


def override_get_db():
    assert _active_session is not None, "no active per-test session/transaction"
    yield _active_session  # not closed here — the _db_transaction fixture owns it


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def _block_all_emails():
    # auth_service imports send_reset_email by name (`from app.core.mailer
    # import send_reset_email`), so it holds its own reference to the
    # original function — patching `app.core.mailer.send_reset_email` alone
    # does not intercept calls made through that reference. Patch it at
    # its actual call site too, or a background task can slip through and
    # write a real SentEmail row via its own SessionLocal(), which isn't
    # covered by the per-test transaction rollback and leaks into later
    # tests' counts.
    with (
        patch("app.core.mailer.send_reset_email"),
        patch("app.services.auth_service.send_reset_email"),
        patch("app.core.mailer._send_to_multiple"),
    ):
        yield


_original_gensalt = bcrypt.gensalt


@pytest.fixture(scope="session", autouse=True)
def _fast_bcrypt():
    bcrypt.gensalt = lambda rounds=4, prefix=b"2b": _original_gensalt(  # noqa: ARG005
        rounds=4, prefix=prefix
    )
    yield
    bcrypt.gensalt = _original_gensalt


@pytest.fixture(scope="session", autouse=True)
def _moto_env():
    with mock_aws():
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        client.create_bucket(Bucket="test-bucket")
        yield


@pytest.fixture(autouse=True)
def mock_s3(_moto_env):
    old_singleton = storage_module._storage
    storage_module._storage = None

    storage = StorageClient(
        endpoint_url="https://s3.amazonaws.com",
        access_key="testing",
        secret_key="testing",
        bucket="test-bucket",
    )
    app.dependency_overrides[get_storage] = lambda: storage
    yield storage
    app.dependency_overrides.pop(get_storage, None)
    storage_module._storage = old_singleton


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="https://testserver") as c:
        yield c
