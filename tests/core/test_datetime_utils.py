"""Regression tests for app/core/datetime_utils.py — the single source of
truth for Settings.app_timezone-based wall-clock time (see the 2026-08-15
timezone audit). Pacific/Kiritimati (UTC+14) is used throughout as "a zone
that is never equal to UTC" — these tests reliably fail if the code under
test reverts to datetime.now(UTC), unlike a comparison against
Europe/Vienna, which only diverges from UTC during part of the day."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.core.datetime_utils import (
    get_app_timezone,
    local_day_bounds_utc,
    local_now,
    local_today,
)


class TestGetAppTimezone:
    def test_defaults_to_europe_vienna(self, monkeypatch) -> None:
        monkeypatch.delenv("APP_TIMEZONE", raising=False)
        assert get_app_timezone() == ZoneInfo("Europe/Vienna")

    def test_honors_app_timezone_override(self, monkeypatch) -> None:
        monkeypatch.setenv("APP_TIMEZONE", "Pacific/Kiritimati")
        assert get_app_timezone() == ZoneInfo("Pacific/Kiritimati")


class TestLocalNow:
    def test_returns_aware_datetime_in_configured_zone(self, monkeypatch) -> None:
        monkeypatch.setenv("APP_TIMEZONE", "Pacific/Kiritimati")
        now = local_now()
        assert now.tzinfo is not None
        expected = datetime.now(ZoneInfo("Pacific/Kiritimati"))
        assert now.utcoffset() == expected.utcoffset()

    def test_differs_from_utc_now_by_the_configured_offset(self, monkeypatch) -> None:
        monkeypatch.setenv("APP_TIMEZONE", "Pacific/Kiritimati")
        local = local_now()
        utc_now = datetime.now(UTC)
        # UTC+14 — always at least several hours apart from UTC, so this
        # can never pass by accident the way a Europe/Vienna comparison
        # could near midnight.
        assert abs((local.astimezone(UTC) - utc_now).total_seconds()) < 5
        assert local.utcoffset().total_seconds() == 14 * 3600  # type: ignore[union-attr]


class TestLocalToday:
    def test_matches_local_now_date(self, monkeypatch) -> None:
        monkeypatch.setenv("APP_TIMEZONE", "Pacific/Kiritimati")
        assert local_today() == local_now().date()

    def test_can_differ_from_utc_today(self, monkeypatch) -> None:
        # Not a fixed assertion about *which* day it is (that depends on
        # wall-clock time when the suite runs) — just proves local_today()
        # is computed from the configured zone, not from datetime.now(UTC)
        # truncated to a date, by comparing it against an independently
        # computed reference.
        monkeypatch.setenv("APP_TIMEZONE", "Pacific/Kiritimati")
        expected = datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
        assert local_today() == expected


class TestLocalDayBoundsUtc:
    def test_ordinary_day_spans_24_hours(self, monkeypatch) -> None:
        monkeypatch.setenv("APP_TIMEZONE", "Europe/Vienna")
        start, end = local_day_bounds_utc(date(2026, 6, 15))
        assert (end - start).total_seconds() == 24 * 3600
        assert start.tzinfo is UTC
        assert end.tzinfo is UTC

    def test_spring_forward_day_spans_23_hours(self, monkeypatch) -> None:
        # 2026-03-29: Europe/Vienna's spring DST transition (02:00 -> 03:00).
        monkeypatch.setenv("APP_TIMEZONE", "Europe/Vienna")
        start, end = local_day_bounds_utc(date(2026, 3, 29))
        assert (end - start).total_seconds() == 23 * 3600

    def test_fall_back_day_spans_25_hours(self, monkeypatch) -> None:
        # 2026-10-25: Europe/Vienna's autumn DST transition (03:00 -> 02:00).
        monkeypatch.setenv("APP_TIMEZONE", "Europe/Vienna")
        start, end = local_day_bounds_utc(date(2026, 10, 25))
        assert (end - start).total_seconds() == 25 * 3600

    def test_bounds_are_the_local_calendar_days_midnight(self, monkeypatch) -> None:
        monkeypatch.setenv("APP_TIMEZONE", "Pacific/Kiritimati")
        day = date(2026, 6, 15)
        start, end = local_day_bounds_utc(day)
        tz = ZoneInfo("Pacific/Kiritimati")
        assert start.astimezone(tz) == datetime(2026, 6, 15, 0, 0, tzinfo=tz)
        assert end.astimezone(tz) == datetime(2026, 6, 16, 0, 0, tzinfo=tz)
