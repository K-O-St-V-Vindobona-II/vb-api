"""Direct service-layer tests for app.services.tracking_service.

The full request/response behavior (permissions, pagination, filtering)
is already covered end-to-end via tests/test_tracking.py. These tests
target the session-grouping algorithm's internal branches directly,
since building two distinct sessions through the HTTP layer would need
much heavier fixtures than calling the pure grouping functions directly.
"""

from datetime import UTC, datetime, timedelta

from app.models.member import Member
from app.models.org import Org
from app.models.request_log import RequestLog
from app.services.tracking_service import (
    _group_logs_into_sessions,
    _is_session_boundary,
    _member_name_map,
)


def _log(member_id: int, created_at: datetime, log_id: int = 1) -> RequestLog:
    return RequestLog(
        id=log_id,
        client_ip="127.0.0.1",
        member_id=member_id,
        request_method="GET",
        request_path="/api/test",
        response_status=200,
        created_at=created_at,
    )


class TestIsSessionBoundary:
    def test_different_member_is_boundary(self) -> None:
        now = datetime.now(UTC)
        log = _log(member_id=2, created_at=now)
        assert _is_session_boundary(log, 1, [], timedelta(minutes=30)) is True

    def test_same_member_empty_group_is_not_boundary(self) -> None:
        now = datetime.now(UTC)
        log = _log(member_id=1, created_at=now)
        assert _is_session_boundary(log, 1, [], timedelta(minutes=30)) is False

    def test_same_member_within_gap_is_not_boundary(self) -> None:
        now = datetime.now(UTC)
        previous = _log(member_id=1, created_at=now)
        log = _log(member_id=1, created_at=now + timedelta(minutes=10))
        assert _is_session_boundary(log, 1, [previous], timedelta(minutes=30)) is False

    def test_same_member_beyond_gap_is_boundary(self) -> None:
        now = datetime.now(UTC)
        previous = _log(member_id=1, created_at=now)
        log = _log(member_id=1, created_at=now + timedelta(minutes=45))
        assert _is_session_boundary(log, 1, [previous], timedelta(minutes=30)) is True


class TestGroupLogsIntoSessions:
    def test_gap_beyond_threshold_splits_into_two_sessions(self) -> None:
        now = datetime.now(UTC)
        logs = [
            _log(member_id=1, created_at=now, log_id=1),
            _log(member_id=1, created_at=now + timedelta(minutes=5), log_id=2),
            _log(member_id=1, created_at=now + timedelta(hours=2), log_id=3),
        ]

        sessions = _group_logs_into_sessions(logs, {1: "Test User"})

        assert len(sessions) == 2
        assert sessions[0].action_count == 2
        assert sessions[1].action_count == 1

    def test_different_members_produce_separate_sessions(self) -> None:
        now = datetime.now(UTC)
        logs = [
            _log(member_id=1, created_at=now, log_id=1),
            _log(member_id=2, created_at=now, log_id=2),
        ]

        sessions = _group_logs_into_sessions(logs, {1: "User One", 2: "User Two"})

        assert [s.member_id for s in sessions] == [1, 2]


class TestMemberNameMap:
    def test_maps_ids_to_full_names(self, db_session) -> None:
        db_session.add(Org(id="vbw", label="VBW", order=1))
        db_session.commit()
        member = Member(
            email="name-map@vbw.at",
            vorname="Vorname",
            nachname="Nachname",
            org_id="vbw",
        )
        db_session.add(member)
        db_session.commit()

        names = _member_name_map(db_session, {member.id})

        assert names == {member.id: "Vorname Nachname"}
