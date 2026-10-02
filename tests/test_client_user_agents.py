"""The User-Agent header is client-controlled: it must neither hide a request
from the activity log nor grow the table without a bound per row."""

import secrets
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.client_user_agent import MAX_USER_AGENT_LENGTH, ClientUserAgent
from app.models.request_log import RequestLog
from app.services import activity_log_service
from app.services.activity_log_service import (
    _get_or_create_client_user_agent,
    record_request,
)


def _record(db, user_agent: str | None) -> None:
    record_request(
        db,
        client_ip="203.0.113.9",
        client_ips=["203.0.113.9"],
        auth_header=None,
        user_agent_string=user_agent,
        request_method="POST",
        request_path="/api/auth/login",
        request_input=None,
        response_status=401,
        response_content=None,
        memory_usage=0,
    )


class TestGetOrCreateClientUserAgent:
    def test_same_value_returns_the_same_row(self, db_session):
        first = _get_or_create_client_user_agent(db_session, "Mozilla/5.0 test")
        second = _get_or_create_client_user_agent(db_session, "Mozilla/5.0 test")

        assert first == second
        assert db_session.query(ClientUserAgent).count() == 1

    def test_over_long_value_is_cut_to_the_bound(self, db_session):
        long_value = "x" * (MAX_USER_AGENT_LENGTH + 500)

        row_id = _get_or_create_client_user_agent(db_session, long_value)

        stored = db_session.get(ClientUserAgent, row_id)
        assert stored is not None
        assert stored.string == "x" * MAX_USER_AGENT_LENGTH

    def test_values_that_differ_only_after_the_bound_share_a_row(self, db_session):
        prefix = "y" * MAX_USER_AGENT_LENGTH

        first = _get_or_create_client_user_agent(db_session, prefix + "one")
        second = _get_or_create_client_user_agent(db_session, prefix + "two")

        assert first == second

    def test_lost_insert_race_returns_the_winners_row(self, db_session, monkeypatch):
        winner = _get_or_create_client_user_agent(db_session, "raced-agent")
        # The first look-up misses although the row exists, as it does when a
        # concurrent request inserted it between look-up and insert.
        misses = iter([True])
        real_find = activity_log_service._find_client_user_agent_id

        def find_with_one_miss(db, value):
            return None if next(misses, False) else real_find(db, value)

        monkeypatch.setattr(
            activity_log_service, "_find_client_user_agent_id", find_with_one_miss
        )

        loser = _get_or_create_client_user_agent(db_session, "raced-agent")

        assert loser == winner
        assert db_session.query(ClientUserAgent).count() == 1


class TestDatabaseBound:
    def test_value_over_the_bound_is_rejected_by_the_database(self, db_session):
        db_session.add(ClientUserAgent(string="z" * (MAX_USER_AGENT_LENGTH + 1)))

        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_value_at_the_bound_is_accepted(self, db_session):
        db_session.add(ClientUserAgent(string="z" * MAX_USER_AGENT_LENGTH))

        db_session.flush()


class TestRequestsWithHostileUserAgents:
    def test_incompressible_multi_kilobyte_value_is_still_logged(self, db_session):
        # About 3.4 KB of random hex: over the btree row limit of the unique
        # index, which used to make the insert fail and drop the log entry.
        _record(db_session, secrets.token_hex(1700))

        assert db_session.query(RequestLog).count() == 1
        assert db_session.query(ClientUserAgent).one().string.__len__() == (
            MAX_USER_AGENT_LENGTH
        )

    def test_request_without_user_agent_is_logged_without_a_row(self, db_session):
        _record(db_session, None)

        log = db_session.query(RequestLog).one()
        assert log.client_user_agent_id is None
        assert db_session.query(ClientUserAgent).count() == 0

    def test_distinct_values_each_get_their_own_bounded_row(self, db_session):
        for _ in range(3):
            _record(db_session, f"agent-{uuid.uuid4()}")

        assert db_session.query(ClientUserAgent).count() == 3
