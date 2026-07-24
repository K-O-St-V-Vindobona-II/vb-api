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
    get_activity_sessions,
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


class TestGetActivitySessionsPagination:
    """Sessions can't be paginated at the raw-row query level (would split
    a session across pages) - pagination applies to the grouped session
    list instead. Regression test for that slicing."""

    def test_paginates_grouped_sessions_not_raw_rows(self, db_session) -> None:
        now = datetime.now(UTC)
        for member_id, offset_hours in [(1, 0), (2, 0), (3, 0)]:
            db_session.add(
                RequestLog(
                    client_ip="127.0.0.1",
                    member_id=member_id,
                    request_method="GET",
                    request_path="/api/test",
                    response_status=200,
                    memory_usage=0,
                    created_at=now + timedelta(hours=offset_hours),
                    updated_at=now,
                )
            )
        db_session.commit()

        page1 = get_activity_sessions(
            db_session, now.strftime("%Y-%m-%d"), None, page=1, page_size=2
        )
        page2 = get_activity_sessions(
            db_session, now.strftime("%Y-%m-%d"), None, page=2, page_size=2
        )

        assert page1["total"] == 3
        assert len(page1["items"]) == 2
        assert page2["total"] == 3
        assert len(page2["items"]) == 1
        assert page1["items"][0].member_id != page2["items"][0].member_id


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
