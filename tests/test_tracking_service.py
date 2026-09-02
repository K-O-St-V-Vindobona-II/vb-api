"""Direct service-layer tests for app.services.tracking_service.

The full request/response behavior (permissions, pagination, filtering)
is already covered end-to-end via tests/test_tracking.py. These tests
target the session-grouping algorithm's internal branches directly,
since building two distinct sessions through the HTTP layer would need
much heavier fixtures than calling the pure grouping functions directly.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from app.core.datetime_utils import local_day_bounds_utc, local_today
from app.models.member import Member
from app.models.org import Org
from app.models.request_log import RequestLog
from app.services.tracking_service import (
    _group_logs_into_sessions,
    _is_session_boundary,
    _member_name_map,
    get_activity_sessions,
    list_activity,
)


def _log(member_id: uuid.UUID, created_at: datetime, log_id: int = 1) -> RequestLog:
    return RequestLog(
        id=log_id,
        client_ip="127.0.0.1",
        member_id=member_id,
        request_method="GET",
        request_path="/api/test",
        response_status=200,
        created_at=created_at,
    )


def _create_member(db_session, **overrides: object) -> Member:
    if not db_session.get(Org, "vbw"):
        db_session.add(Org(id="vbw", label="VBW", order=1))
    member = Member(
        email=overrides.pop("email", f"{uuid.uuid4()}@vbw.at"),
        vorname="Test",
        nachname="User",
        org_id="vbw",
        **overrides,
    )
    db_session.add(member)
    db_session.commit()
    return member


class TestIsSessionBoundary:
    def test_different_member_is_boundary(self) -> None:
        now = datetime.now(UTC)
        member_a, member_b = uuid.uuid4(), uuid.uuid4()
        log = _log(member_id=member_b, created_at=now)
        assert _is_session_boundary(log, member_a, [], timedelta(minutes=30)) is True

    def test_same_member_empty_group_is_not_boundary(self) -> None:
        now = datetime.now(UTC)
        member = uuid.uuid4()
        log = _log(member_id=member, created_at=now)
        assert _is_session_boundary(log, member, [], timedelta(minutes=30)) is False

    def test_same_member_within_gap_is_not_boundary(self) -> None:
        now = datetime.now(UTC)
        member = uuid.uuid4()
        previous = _log(member_id=member, created_at=now)
        log = _log(member_id=member, created_at=now + timedelta(minutes=10))
        assert (
            _is_session_boundary(log, member, [previous], timedelta(minutes=30))
            is False
        )

    def test_same_member_beyond_gap_is_boundary(self) -> None:
        now = datetime.now(UTC)
        member = uuid.uuid4()
        previous = _log(member_id=member, created_at=now)
        log = _log(member_id=member, created_at=now + timedelta(minutes=45))
        assert (
            _is_session_boundary(log, member, [previous], timedelta(minutes=30)) is True
        )


class TestGroupLogsIntoSessions:
    def test_gap_beyond_threshold_splits_into_two_sessions(self) -> None:
        now = datetime.now(UTC)
        member = uuid.uuid4()
        logs = [
            _log(member_id=member, created_at=now, log_id=1),
            _log(member_id=member, created_at=now + timedelta(minutes=5), log_id=2),
            _log(member_id=member, created_at=now + timedelta(hours=2), log_id=3),
        ]

        sessions = _group_logs_into_sessions(logs, {member: "Test User"})

        assert len(sessions) == 2
        assert sessions[0].action_count == 2
        assert sessions[1].action_count == 1

    def test_different_members_produce_separate_sessions(self) -> None:
        now = datetime.now(UTC)
        member_a, member_b = uuid.uuid4(), uuid.uuid4()
        logs = [
            _log(member_id=member_a, created_at=now, log_id=1),
            _log(member_id=member_b, created_at=now, log_id=2),
        ]

        sessions = _group_logs_into_sessions(
            logs, {member_a: "User One", member_b: "User Two"}
        )

        assert [s.member_id for s in sessions] == [member_a, member_b]


class TestGetActivitySessionsPagination:
    """Sessions can't be paginated at the raw-row query level (would split
    a session across pages) - pagination applies to the grouped session
    list instead. Regression test for that slicing."""

    def test_paginates_grouped_sessions_not_raw_rows(self, db_session) -> None:
        # Anchored to local_today()'s own UTC day bounds (not a raw
        # datetime.now(UTC) timestamp/strftime) so this stays correct
        # regardless of when the suite runs relative to Vienna midnight;
        # get_activity_sessions() interprets date_str as a Vienna-local
        # calendar day (see the 2026-08-15 timezone audit).
        today = local_today()
        day_start, _ = local_day_bounds_utc(today)
        base = day_start + timedelta(hours=12)
        members = [_create_member(db_session) for _ in range(3)]
        for member in members:
            db_session.add(
                RequestLog(
                    client_ip="127.0.0.1",
                    member_id=member.id,
                    request_method="GET",
                    request_path="/api/test",
                    response_status=200,
                    memory_usage=0,
                    created_at=base,
                    updated_at=base,
                )
            )
        db_session.commit()

        page1 = get_activity_sessions(
            db_session, today.isoformat(), None, page=1, page_size=2
        )
        page2 = get_activity_sessions(
            db_session, today.isoformat(), None, page=2, page_size=2
        )

        assert page1["total"] == 3
        assert len(page1["items"]) == 2
        assert page2["total"] == 3
        assert len(page2["items"]) == 1
        assert page1["items"][0].member_id != page2["items"][0].member_id


class TestGetActivitySessionsTimezone:
    def test_date_str_interpreted_as_configured_app_timezone(
        self, db_session, monkeypatch
    ) -> None:
        """Regression (2026-08-15 timezone audit): date_str previously
        became datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC),
        a Vienna-local calendar day picked in the frontend's DatePicker
        was silently reinterpreted as a UTC day, shifting the actual
        window by 1-2h. Pacific/Kiritimati (UTC+14) makes the local and
        UTC day boundaries differ by a full 14h, so a log placed inside
        that gap unambiguously proves which basis is actually used."""
        monkeypatch.setenv("APP_TIMEZONE", "Pacific/Kiritimati")
        member = _create_member(db_session)
        day = date(2026, 6, 15)
        day_start_utc, _ = local_day_bounds_utc(day)
        # 30 minutes into the Kiritimati-local day, but still 2026-06-14 in
        # UTC; a UTC-based interpretation of "2026-06-15" would exclude
        # this row entirely.
        inside_local_day = day_start_utc + timedelta(minutes=30)

        db_session.add(
            RequestLog(
                client_ip="127.0.0.1",
                member_id=member.id,
                request_method="GET",
                request_path="/api/test",
                response_status=200,
                memory_usage=0,
                created_at=inside_local_day,
                updated_at=inside_local_day,
            )
        )
        db_session.commit()

        result = get_activity_sessions(
            db_session, day.isoformat(), None, page=1, page_size=25
        )
        assert result["total"] == 1


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


class TestListActivityQueryCount:
    """Regression test for N+1s in _member_name_map(): resolving names
    for every distinct member behind a page of logs must stay a single
    batch query, not scale with the number of distinct members on that
    page (each entry's now-UUID member_id makes an accidental per-row
    lookup easy to introduce unnoticed)."""

    def test_query_count_does_not_scale_with_distinct_members(
        self, db_session, count_queries
    ) -> None:
        now = datetime.now(UTC)
        few_members = [_create_member(db_session) for _ in range(2)]
        for member in few_members:
            db_session.add(
                RequestLog(
                    client_ip="127.0.0.1",
                    member_id=member.id,
                    request_method="GET",
                    request_path="/api/test",
                    response_status=200,
                    memory_usage=0,
                    created_at=now,
                    updated_at=now,
                )
            )
        db_session.commit()

        with count_queries() as few:
            list_activity(
                db_session, 1, 25, member_id=None, date_from=None, date_to=None
            )

        many_members = [_create_member(db_session) for _ in range(8)]
        for member in many_members:
            db_session.add(
                RequestLog(
                    client_ip="127.0.0.1",
                    member_id=member.id,
                    request_method="GET",
                    request_path="/api/test",
                    response_status=200,
                    memory_usage=0,
                    created_at=now,
                    updated_at=now,
                )
            )
        db_session.commit()

        with count_queries() as many:
            list_activity(
                db_session, 1, 25, member_id=None, date_from=None, date_to=None
            )

        assert many.count == few.count
