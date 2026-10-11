"""A refresh secret is single use even when two requests present it together.

The race needs two real database connections with committed data, so these
tests create their rows outside the per-test transaction and remove them
afterwards."""

import threading
import time
from unittest.mock import patch

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.core import security
from app.core.security import hash_refresh_secret, verify_refresh_secret
from app.db.database import engine
from app.models.auth_session import AuthSession
from app.models.member import Member
from app.services import auth_service

WAIT_SECONDS = 5


@pytest.fixture
def committed_session():
    """(session id, refresh secret) of a session committed for real."""
    setup = Session(bind=engine)
    member = Member(email="refresh-race@example.at", auth_locked=False)
    setup.add(member)
    setup.commit()
    _, session_id, secret = auth_service.create_user_session(setup, member)
    member_id = member.id
    setup.close()
    try:
        yield session_id, secret
    finally:
        cleanup = Session(bind=engine)
        cleanup.query(Member).filter(Member.id == member_id).delete()
        cleanup.commit()
        cleanup.close()


def _wait_until_a_request_waits_for_a_lock() -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    with engine.connect() as watcher:
        while time.monotonic() < deadline:
            waiting = watcher.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND datname = current_database()"
                )
            ).scalar()
            if waiting:
                return
            time.sleep(0.02)
    pytest.fail("no request waited for a lock")


class TestConcurrentRefresh:
    def test_the_second_request_sees_the_rotated_secret_and_is_rejected(
        self, committed_session
    ):
        session_id, secret = committed_session
        first = Session(bind=engine)
        first.execute(
            text("SELECT id FROM sessions WHERE jti = :jti FOR UPDATE"),
            {"jti": session_id},
        )
        outcome: dict[str, object] = {}

        def second_request() -> None:
            second = Session(bind=engine)
            try:
                outcome["result"] = auth_service.refresh_session(
                    second, session_id, secret
                )
            except ValueError as error:
                outcome["error"] = str(error)
            finally:
                second.close()

        thread = threading.Thread(target=second_request)
        thread.start()
        try:
            _wait_until_a_request_waits_for_a_lock()
            # The first request now rotates the secret and commits.
            first.execute(
                text("UPDATE sessions SET refresh_token_hash = :hash WHERE jti = :jti"),
                {
                    "hash": hash_refresh_secret("rotated-by-the-first"),
                    "jti": session_id,
                },
            )
            first.commit()
        finally:
            first.close()
            thread.join(timeout=WAIT_SECONDS)

        assert not thread.is_alive()
        assert outcome == {"error": "Token reuse detected"}

    def test_a_single_request_is_not_delayed_and_rotates_the_secret(
        self, committed_session
    ):
        session_id, secret = committed_session
        db = Session(bind=engine)
        try:
            _, _, new_secret = auth_service.refresh_session(db, session_id, secret)
            stored = (
                db.query(AuthSession.refresh_token_hash)
                .filter(AuthSession.jti == session_id)
                .scalar()
            )
        finally:
            db.close()

        assert new_secret != secret
        assert stored == hash_refresh_secret(new_secret)


class TestRowIsLocked:
    def test_the_session_row_is_read_with_for_update(self, db_session):
        member = Member(email="lock-statement@example.at", auth_locked=False)
        db_session.add(member)
        db_session.commit()
        _, session_id, secret = auth_service.create_user_session(db_session, member)
        statements: list[str] = []

        def record(_conn, _cursor, statement, *_args):
            statements.append(statement)

        bind = db_session.get_bind()
        event.listen(bind, "before_cursor_execute", record)
        try:
            auth_service.refresh_session(db_session, session_id, secret)
        finally:
            event.remove(bind, "before_cursor_execute", record)

        session_reads = [s for s in statements if "FROM sessions" in s]
        assert session_reads
        assert all("FOR UPDATE" in s for s in session_reads[:1])


class TestSecretComparison:
    def test_the_digest_is_compared_in_constant_time(self):
        with patch.object(security.hmac, "compare_digest", return_value=True) as spy:
            assert verify_refresh_secret("whatever", "not-the-digest") is True

        spy.assert_called_once()

    def test_a_matching_secret_is_accepted_and_others_are_not(self):
        digest = hash_refresh_secret("the-secret")

        assert verify_refresh_secret("the-secret", digest)
        assert not verify_refresh_secret("the-secreT", digest)
        assert not verify_refresh_secret("", digest)
        assert not verify_refresh_secret("the-secret", "")
