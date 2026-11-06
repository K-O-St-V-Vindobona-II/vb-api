"""Connection settings of the shared database engine."""

from unittest.mock import MagicMock, patch

import pytest

from app.db.database import engine, get_db


class TestEngineLimits:
    def test_a_process_holds_at_most_fifteen_connections(self):
        # Two gunicorn workers and the job worker add up to 45 connections,
        # well below PostgreSQL's default limit of 100 that pg_dump and the
        # other container share.
        pool = engine.pool

        assert pool.size() == 5
        assert pool._max_overflow == 10

    def test_waiting_for_a_free_connection_is_bounded(self):
        assert engine.pool.timeout() == 10

    def test_connecting_to_the_database_is_bounded(self):
        with engine.connect() as connection:
            parameters = connection.connection.dbapi_connection.get_dsn_parameters()

        assert parameters["connect_timeout"] == "5"

    def test_fifteen_connections_can_be_used_at_once(self):
        connections = [engine.connect() for _ in range(15)]
        try:
            assert engine.pool.checkedout() == 15
        finally:
            for connection in connections:
                connection.close()


class TestGetDb:
    def test_the_session_is_closed_once_the_request_is_done(self):
        session = MagicMock()

        with patch("app.db.database.SessionLocal", return_value=session):
            dependency = get_db()
            assert next(dependency) is session
            session.close.assert_not_called()
            with pytest.raises(StopIteration):
                next(dependency)

        session.close.assert_called_once_with()

    def test_the_session_is_closed_when_the_request_fails(self):
        session = MagicMock()

        with patch("app.db.database.SessionLocal", return_value=session):
            dependency = get_db()
            next(dependency)
            with pytest.raises(RuntimeError, match="boom"):
                dependency.throw(RuntimeError("boom"))

        session.close.assert_called_once_with()
