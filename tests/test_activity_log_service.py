"""Direct service-layer tests for app.services.activity_log_service.

The full request/response behavior is covered end-to-end via
tests/test_activity_log.py (router) and tests/test_activity_logger.py
(middleware). These tests target the pure functions and the day/member
grouping directly.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.core.security import create_access_token
from app.models.client_user_agent import ClientUserAgent
from app.models.member import Member
from app.models.org import Org
from app.models.request_log import RequestLog
from app.services.activity_log_service import (
    cap_stored_size,
    get_entry,
    list_days_with_activity,
    list_entries_for_member_day,
    record_request,
    redact,
    should_skip,
)


def _create_member(db_session, **overrides: object) -> Member:
    if not db_session.get(Org, "vbw"):
        db_session.add(Org(id="vbw", label="VBW", order=1))
    member = Member(
        email=overrides.pop("email", f"{uuid.uuid4()}@vbw.at"),
        vorname=overrides.pop("vorname", "Test"),
        nachname=overrides.pop("nachname", "User"),
        org_id="vbw",
        **overrides,
    )
    db_session.add(member)
    db_session.commit()
    return member


class TestShouldSkip:
    def test_skips_root_health_check(self) -> None:
        assert should_skip("/", skip_header_present=False) is True

    def test_skips_docs_and_openapi(self) -> None:
        assert should_skip("/docs", skip_header_present=False) is True
        assert should_skip("/openapi.json", skip_header_present=False) is True

    def test_skips_when_opt_out_header_present(self) -> None:
        assert should_skip("/api/anything", skip_header_present=True) is True

    def test_logs_everything_else_without_an_endpoint_allowlist(self) -> None:
        assert should_skip("/api/standesdb/members", skip_header_present=False) is False
        assert (
            should_skip("/api/p4x/admin/accounts", skip_header_present=False) is False
        )
        assert should_skip("/api/archive/dirs", skip_header_present=False) is False


class TestRedact:
    def test_masks_known_keys_case_insensitively(self) -> None:
        result = redact({"Password": "secret", "email": "a@b.at"})
        assert result == {"Password": "***", "email": "a@b.at"}

    def test_masks_nested_dicts_and_lists(self) -> None:
        result = redact({"data": [{"access_token": "xyz"}, {"ok": True}]})
        assert result == {"data": [{"access_token": "***"}, {"ok": True}]}

    def test_passes_through_non_dict_scalars(self) -> None:
        assert redact("plain string") == "plain string"
        assert redact(None) is None
        assert redact(42) == 42


class TestCapStoredSize:
    def test_passes_through_small_values(self) -> None:
        assert cap_stored_size({"a": 1}) == {"a": 1}

    def test_returns_none_for_oversized_values(self) -> None:
        huge = {"blob": "x" * 100_000}
        assert cap_stored_size(huge) is None

    def test_passes_through_none(self) -> None:
        assert cap_stored_size(None) is None


class TestRecordRequest:
    def test_persists_entry_with_redacted_input(self, db_session) -> None:
        record_request(
            db_session,
            client_ip="203.0.113.5",
            client_ips=["203.0.113.5"],
            auth_header=None,
            user_agent_string="pytest-agent",
            request_method="POST",
            request_path="/api/auth/login",
            request_input={"password": "secret", "email": "a@b.at"},
            response_status=200,
            response_content=None,
            memory_usage=1024,
        )

        entry = db_session.query(RequestLog).one()
        assert entry.client_ip == "203.0.113.5"
        assert entry.request_input == {"password": "***", "email": "a@b.at"}
        assert entry.client_user_agent_id is not None

    def test_resolves_member_id_from_bearer_token(self, db_session) -> None:
        member = _create_member(db_session, email="found@vbw.at")
        token, _ = create_access_token(subject="found@vbw.at")

        record_request(
            db_session,
            client_ip="1.2.3.4",
            client_ips=["1.2.3.4"],
            auth_header=f"Bearer {token}",
            user_agent_string=None,
            request_method="GET",
            request_path="/api/standesdb/members/1",
            request_input=None,
            response_status=200,
            response_content=None,
            memory_usage=0,
        )

        entry = db_session.query(RequestLog).one()
        assert entry.member_id == member.id

    def test_ignores_malformed_auth_header(self, db_session) -> None:
        record_request(
            db_session,
            client_ip="1.2.3.4",
            client_ips=["1.2.3.4"],
            auth_header="not-a-bearer-token",
            user_agent_string=None,
            request_method="GET",
            request_path="/api/x",
            request_input=None,
            response_status=200,
            response_content=None,
            memory_usage=0,
        )

        entry = db_session.query(RequestLog).one()
        assert entry.member_id is None

    def test_reuses_existing_client_user_agent(self, db_session) -> None:
        record_request(
            db_session,
            client_ip="1.2.3.4",
            client_ips=["1.2.3.4"],
            auth_header=None,
            user_agent_string="Mozilla/5.0 Test",
            request_method="GET",
            request_path="/api/a",
            request_input=None,
            response_status=200,
            response_content=None,
            memory_usage=0,
        )
        record_request(
            db_session,
            client_ip="1.2.3.4",
            client_ips=["1.2.3.4"],
            auth_header=None,
            user_agent_string="Mozilla/5.0 Test",
            request_method="GET",
            request_path="/api/b",
            request_input=None,
            response_status=200,
            response_content=None,
            memory_usage=0,
        )

        assert db_session.query(ClientUserAgent).count() == 1


class TestListDaysWithActivity:
    def test_groups_by_local_calendar_day(self, db_session) -> None:
        member = _create_member(db_session)
        day_one = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
        day_two = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
        for created_at in (day_one, day_two):
            db_session.add(
                RequestLog(
                    client_ip="127.0.0.1",
                    client_ips=["127.0.0.1"],
                    member_id=member.id,
                    request_method="GET",
                    request_path="/api/test",
                    response_status=200,
                    memory_usage=0,
                    created_at=created_at,
                )
            )
        db_session.commit()

        groups = list_days_with_activity(db_session, 2026, 6)

        assert [g.day.isoformat() for g in groups] == ["2026-06-16", "2026-06-15"]
        assert groups[0].members[0].id == str(member.id)

    def test_ignores_entries_without_a_member(self, db_session) -> None:
        now = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
        db_session.add(
            RequestLog(
                client_ip="127.0.0.1",
                client_ips=["127.0.0.1"],
                member_id=None,
                request_method="POST",
                request_path="/api/auth/login",
                response_status=401,
                memory_usage=0,
                created_at=now,
            )
        )
        db_session.commit()

        assert list_days_with_activity(db_session, 2026, 6) == []

    def test_empty_month_returns_empty_list(self, db_session) -> None:
        assert list_days_with_activity(db_session, 2020, 1) == []

    def test_query_count_does_not_scale_with_distinct_members(
        self, db_session, count_queries
    ) -> None:
        """N+1 regression guard: resolving member labels for a day's
        activity must stay a fixed number of queries regardless of how
        many distinct members were active that month."""
        now = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)

        few_members = [_create_member(db_session) for _ in range(2)]
        for member in few_members:
            db_session.add(
                RequestLog(
                    client_ip="127.0.0.1",
                    client_ips=["127.0.0.1"],
                    member_id=member.id,
                    request_method="GET",
                    request_path="/api/test",
                    response_status=200,
                    memory_usage=0,
                    created_at=now,
                )
            )
        db_session.commit()

        with count_queries() as few:
            list_days_with_activity(db_session, 2026, 6)

        many_members = [_create_member(db_session) for _ in range(8)]
        for member in many_members:
            db_session.add(
                RequestLog(
                    client_ip="127.0.0.1",
                    client_ips=["127.0.0.1"],
                    member_id=member.id,
                    request_method="GET",
                    request_path="/api/test",
                    response_status=200,
                    memory_usage=0,
                    created_at=now,
                )
            )
        db_session.commit()

        with count_queries() as many:
            list_days_with_activity(db_session, 2026, 6)

        assert many.count == few.count


class TestListEntriesForMemberDay:
    def test_returns_entries_ordered_by_time(self, db_session) -> None:
        member = _create_member(db_session)
        base = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
        for offset, path in ((2, "/api/second"), (0, "/api/first")):
            db_session.add(
                RequestLog(
                    client_ip="127.0.0.1",
                    client_ips=["127.0.0.1"],
                    member_id=member.id,
                    request_method="GET",
                    request_path=path,
                    response_status=200,
                    memory_usage=0,
                    created_at=base + timedelta(hours=offset),
                )
            )
        db_session.commit()

        detail = list_entries_for_member_day(db_session, member.id, base.date())

        assert [e.request_path for e in detail.entries] == [
            "/api/first",
            "/api/second",
        ]

    def test_raises_404_for_unknown_member(self, db_session) -> None:
        with pytest.raises(HTTPException) as exc_info:
            list_entries_for_member_day(
                db_session, uuid.uuid4(), datetime.now(UTC).date()
            )
        assert exc_info.value.status_code == 404


class TestGetEntry:
    def test_returns_detail_with_user_agent_and_member_name(self, db_session) -> None:
        member = _create_member(db_session, vorname="Vorname", nachname="Nachname")
        ua = ClientUserAgent(string="Mozilla/5.0 TestAgent")
        db_session.add(ua)
        db_session.flush()
        log = RequestLog(
            client_ip="127.0.0.1",
            client_ips=["127.0.0.1"],
            member_id=member.id,
            client_user_agent_id=ua.id,
            request_method="GET",
            request_path="/api/test",
            request_input=None,
            response_status=200,
            response_content=None,
            memory_usage=0,
            created_at=datetime.now(UTC),
        )
        db_session.add(log)
        db_session.commit()

        detail = get_entry(db_session, log.id)

        assert detail.member_name == "Vorname Nachname"
        assert detail.client_user_agent == "Mozilla/5.0 TestAgent"

    def test_raises_404_for_unknown_entry(self, db_session) -> None:
        with pytest.raises(HTTPException) as exc_info:
            get_entry(db_session, uuid.uuid4())
        assert exc_info.value.status_code == 404
