"""Shared pytest fixtures.

The test suite runs exclusively against a real PostgreSQL database — see
scripts/README.md for the local/CI test-database setup convention. The
schema is built once per test session via the actual Alembic migrations
(not Base.metadata.create_all()), so the tests also catch model/migration
drift. Each test runs inside an outer transaction (with a SAVEPOINT for the
ORM session) that is always rolled back afterward for isolation.
"""

import os
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

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

from typing import TYPE_CHECKING

import bcrypt
import boto3
import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.core import storage as storage_module
from app.core.arq_pool import get_arq_pool
from app.core.config import get_settings
from app.core.storage import StorageClient, get_storage
from app.db.database import get_db
from main import app

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection

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
def _reset_settings_cache():
    # get_settings() is lru_cache'd for app runtime performance, but tests
    # need each test to see the current os.environ (e.g. monkeypatch.setenv
    # or patch.dict(os.environ, ...) of Tier 1/2/3 vars) — clear the cache
    # before AND after so nothing leaks into neighboring tests.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
    # app.worker imports every send_*_email function by name (`from
    # app.core.mailer import send_reset_email, ...`), so it holds its own
    # references to the original functions — patching
    # `app.core.mailer.send_x` alone does not intercept calls made through
    # those references (e.g. a test that calls a task_send_x_email
    # wrapper directly). Patch each one at its actual call site too, or a
    # task can slip through and write a real SentEmail row via its own
    # SessionLocal(), which isn't covered by the per-test transaction
    # rollback and leaks into later tests' counts.
    with (
        patch("app.core.mailer.send_reset_email"),
        patch("app.worker.send_reset_email"),
        patch("app.worker.send_entry_changed_email"),
        patch("app.worker.send_member_change_request_submitted_email"),
        patch("app.worker.send_member_change_request_resolved_email"),
        patch("app.worker.send_own_image_changed_email"),
        patch("app.core.mailer._send_to_multiple"),
    ):
        yield


@pytest.fixture(autouse=True)
def _block_real_arq_connections():
    """Guards against any test accidentally reaching a real Redis
    connection through the un-overridden get_arq_pool() dependency —
    every test exercising it must override
    app.dependency_overrides[get_arq_pool] instead (see
    tests/test_system.py's TestTriggerDownsync for the pattern)."""
    with patch("app.core.arq_pool.create_pool") as mock_create_pool:
        mock_create_pool.side_effect = AssertionError(
            "A test reached the real get_arq_pool()/create_pool() — "
            "override app.dependency_overrides[get_arq_pool] instead."
        )
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


@pytest.fixture(autouse=True)
def mock_arq_pool():
    """Overrides get_arq_pool for every test with a plain AsyncMock, the
    same way mock_s3 does for get_storage — most tests that create/update
    a member/contact/image/change-request touch this dependency (it's
    resolved on every request, not just ones that end up enqueueing
    anything), so this stays autouse rather than opt-in. Tests that want
    to assert on the actual enqueue_job(...) call request this fixture
    by name to get the same mock instance."""
    pool = AsyncMock()
    app.dependency_overrides[get_arq_pool] = lambda: pool
    yield pool
    app.dependency_overrides.pop(get_arq_pool, None)


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="https://testserver") as c:
        yield c


class QueryCounter:
    """Counts SQL statements executed on the test engine while active."""

    def __init__(self) -> None:
        self.count = 0


@pytest.fixture
def count_queries():
    """Yield a factory for a context manager that counts executed SQL
    statements, e.g. `with count_queries() as counter: ...; assert
    counter.count <= N` — used to assert N+1 query patterns don't regress."""

    @contextmanager
    def _count_queries() -> Iterator[QueryCounter]:
        counter = QueryCounter()

        def _on_execute(
            _conn: Connection,
            _cursor: object,
            _statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            counter.count += 1

        event.listen(engine, "before_cursor_execute", _on_execute)
        try:
            yield counter
        finally:
            event.remove(engine, "before_cursor_execute", _on_execute)

    return _count_queries
