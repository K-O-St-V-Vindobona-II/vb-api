"""Tests for app.services.anniversary_service."""

from datetime import date

from app.models.member import Member
from app.models.org import Org
from app.models.state import State
from app.services.anniversary_service import (
    compute_anniversaries,
    format_date_de,
    get_opted_in_recipients,
    week_window,
)


def _seed_base(db) -> None:
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Org(id="vbn", label="VBN", order=2),
            State(id="fu", label="Fux", order=1),
            State(id="up", label="Urphilister", order=2),
        ]
    )
    db.commit()


def _make_member(db, **overrides) -> Member:
    defaults = {
        "email": "test@vbw.at",
        "vorname": "Test",
        "nachname": "User",
        "couleurname": "Testikus",
        "org_id": "vbw",
        "state_id": "fu",
        "entlassen": False,
        "verstorben": False,
    }
    defaults.update(overrides)
    m = Member(**defaults)
    db.add(m)
    db.commit()
    return m


class TestFormatDateDe:
    def test_format_date_de(self):
        assert format_date_de(date(2026, 1, 5)) == "5. 1. 2026"


class TestWeekWindow:
    def test_tuesday_given_matches_real_cron_trigger_day(self):
        # The scheduler only ever calls compute_anniversaries with a
        # Tuesday `given` (job_standesdb_chronicles' cron trigger day).
        start, end = week_window(date(2026, 7, 14))
        assert start == date(2026, 7, 20)
        assert end == date(2026, 7, 26)

    def test_saturday_given_same_target_week_as_preceding_tuesday(self):
        start, end = week_window(date(2026, 7, 18))
        assert start == date(2026, 7, 20)
        assert end == date(2026, 7, 26)

    def test_sunday_given_same_target_week_as_preceding_tuesday(self):
        start, end = week_window(date(2026, 7, 19))
        assert start == date(2026, 7, 20)
        assert end == date(2026, 7, 26)


class TestGetOptedInRecipients:
    def test_filters_correctly(self, db_session):
        _seed_base(db_session)
        _make_member(db_session, email="opted-in@vbw.at", chroniclemail=True)
        _make_member(db_session, email="opted-out@vbw.at", chroniclemail=False)
        _make_member(
            db_session, email="expelled@vbw.at", chroniclemail=True, entlassen=True
        )
        _make_member(
            db_session, email="deceased@vbw.at", chroniclemail=True, verstorben=True
        )
        _make_member(db_session, email=None, chroniclemail=True)

        recipients = get_opted_in_recipients(db_session)
        assert recipients == ["opted-in@vbw.at"]


class TestComputeAnniversaries:
    def test_finds_birthday_in_coming_week(self, db_session):
        _seed_base(db_session)
        given = date(2026, 7, 14)
        target = date(2026, 7, 22)

        m = _make_member(
            db_session,
            geburtsdatum=date(1990, target.month, target.day),
            geburtsdatum_accuracy=3,
        )
        result = compute_anniversaries(db_session, given)

        assert "vbw" in result
        entries = result["vbw"]["lebend"]["geburtsdatum"]
        assert len(entries) == 1
        assert entries[0]["cn"] == m.cn
        assert entries[0]["years"] == 36
        assert entries[0]["leap_day_note"] is None

    def test_ignores_low_accuracy(self, db_session):
        _seed_base(db_session)
        given = date(2026, 7, 14)
        target = date(2026, 7, 20)

        _make_member(
            db_session,
            geburtsdatum=date(1990, target.month, target.day),
            geburtsdatum_accuracy=1,
        )
        result = compute_anniversaries(db_session, given)
        assert not result

    def test_entlassen_member_excluded(self, db_session):
        _seed_base(db_session)
        given = date(2026, 7, 14)
        target = date(2026, 7, 21)

        _make_member(
            db_session,
            geburtsdatum=date(1990, target.month, target.day),
            geburtsdatum_accuracy=3,
            entlassen=True,
        )
        result = compute_anniversaries(db_session, given)
        assert not result

    def test_deceased_member_in_verstorben_bucket(self, db_session):
        _seed_base(db_session)
        given = date(2026, 7, 14)
        target = date(2026, 7, 21)

        _make_member(
            db_session,
            geburtsdatum=date(1950, target.month, target.day),
            geburtsdatum_accuracy=3,
            verstorben=True,
        )
        result = compute_anniversaries(db_session, given)
        assert "verstorben" in result["vbw"]
        assert "lebend" not in result["vbw"]

    def test_vbn_org_bucketed_separately(self, db_session):
        _seed_base(db_session)
        given = date(2026, 7, 14)
        target = date(2026, 7, 23)

        _make_member(
            db_session,
            email="test@vbn.at",
            org_id="vbn",
            aufnahmedatum=date(2015, target.month, target.day),
            aufnahmedatum_accuracy=3,
        )
        result = compute_anniversaries(db_session, given)
        assert "vbn" in result
        assert result["vbn"]["lebend"]["aufnahmedatum"][0]["years"] == 11

    def test_feb29_anniversary_falls_back_to_feb28_in_non_leap_year(self, db_session):
        _seed_base(db_session)
        given = date(2026, 2, 17)  # Tuesday; target week 2026-02-23..03-01

        m = _make_member(
            db_session,
            geburtsdatum=date(1996, 2, 29),  # 1996 is a leap year
            geburtsdatum_accuracy=3,
        )
        result = compute_anniversaries(db_session, given)

        entries = result["vbw"]["lebend"]["geburtsdatum"]
        assert len(entries) == 1
        assert entries[0]["cn"] == m.cn
        assert entries[0]["date"] == "28. 2. 2026"
        assert entries[0]["years"] == 30
        assert entries[0]["leap_day_note"] == (
            "Jahrestag fällt eigentlich auf den 29. Februar"
        )

    def test_feb29_anniversary_matches_feb29_in_leap_year(self, db_session):
        _seed_base(db_session)
        given = date(2024, 2, 20)  # Tuesday; target week 2024-02-26..03-03

        _make_member(
            db_session,
            geburtsdatum=date(1996, 2, 29),
            geburtsdatum_accuracy=3,
        )
        result = compute_anniversaries(db_session, given)

        entries = result["vbw"]["lebend"]["geburtsdatum"]
        assert len(entries) == 1
        assert entries[0]["date"] == "29. 2. 2024"
        assert entries[0]["years"] == 28
        assert entries[0]["leap_day_note"] is None
