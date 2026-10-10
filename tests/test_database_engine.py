"""Connection settings of the shared database engine."""

from app.db.database import engine


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
