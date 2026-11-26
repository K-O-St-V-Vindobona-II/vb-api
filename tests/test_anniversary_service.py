"""Tests for app.services.anniversary_service."""

from datetime import date

import pytest

from app.models.member import Member
from app.models.org import Org
from app.models.state import State
from app.services.anniversary_service import (
    ANNIVERSARY_FIELDS,
    compute_anniversaries,
    get_opted_in_recipients,
    period_label,
    render_chronicles,
    week_window,
)


def _seed_base(db) -> None:
    db.add_all(
        [
            Org(id="vbw", label="VBW", sort_order=1),
            Org(id="vbn", label="VBN", sort_order=2),
            State(id="fu", label="Fux", sort_order=1),
            State(id="up", label="Urphilister", sort_order=2),
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


class TestPeriodLabel:
    def test_one_year_is_written_once(self):
        assert period_label(date(2027, 2, 22), date(2027, 2, 28)) == (
            "22. Februar bis 28. Februar 2027"
        )

    def test_a_week_over_new_year_names_both_years(self):
        assert period_label(date(2026, 12, 28), date(2027, 1, 3)) == (
            "28. Dezember 2026 bis 3. Jänner 2027"
        )


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
        entries = result["vbw"]["geburtsdatum"]
        assert len(entries) == 1
        assert entries[0]["cn"] == m.cn
        assert entries[0]["deceased"] is False
        assert entries[0]["date"] == date(2026, 7, 22)
        assert (entries[0]["weekday"], entries[0]["day"]) == ("Mi", "22.7.")
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

    def test_deceased_member_is_marked(self, db_session):
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
        assert result["vbw"]["geburtsdatum"][0]["deceased"] is True

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
        assert result["vbn"]["aufnahmedatum"][0]["years"] == 11

    def test_feb29_anniversary_falls_back_to_feb28_in_non_leap_year(self, db_session):
        _seed_base(db_session)
        given = date(2026, 2, 17)  # Tuesday; target week 2026-02-23..03-01

        m = _make_member(
            db_session,
            geburtsdatum=date(1996, 2, 29),  # 1996 is a leap year
            geburtsdatum_accuracy=3,
        )
        result = compute_anniversaries(db_session, given)

        entries = result["vbw"]["geburtsdatum"]
        assert len(entries) == 1
        assert entries[0]["cn"] == m.cn
        assert entries[0]["date"] == date(2026, 2, 28)
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

        entries = result["vbw"]["geburtsdatum"]
        assert len(entries) == 1
        assert entries[0]["date"] == date(2024, 2, 29)
        assert entries[0]["years"] == 28
        assert entries[0]["leap_day_note"] is None


class TestWeekSpanningNewYear:
    @staticmethod
    def _entries(result, field="geburtsdatum"):
        return {e["cn"]: e for e in result["vbw"][field]}

    def test_january_anniversary_belongs_to_the_next_year(self, db_session):
        _seed_base(db_session)
        _make_member(
            db_session,
            email="jan@vbw.at",
            couleurname=None,
            vorname="Jan",
            geburtsdatum=date(1990, 1, 2),
            geburtsdatum_accuracy=3,
        )
        _make_member(
            db_session,
            email="dez@vbw.at",
            couleurname=None,
            vorname="Dez",
            geburtsdatum=date(1985, 12, 30),
            geburtsdatum_accuracy=3,
        )

        result = compute_anniversaries(db_session, date(2026, 12, 27))

        entries = self._entries(result)
        assert entries["Jan User"]["date"] == date(2027, 1, 2)
        assert entries["Jan User"]["years"] == 37
        assert entries["Dez User"]["date"] == date(2026, 12, 30)
        assert entries["Dez User"]["years"] == 41

    def test_window_that_starts_in_january_only_from_a_december_given(self, db_session):
        _seed_base(db_session)
        _make_member(
            db_session,
            couleurname=None,
            vorname="Jan",
            geburtsdatum=date(1990, 1, 5),
            geburtsdatum_accuracy=3,
        )

        result = compute_anniversaries(db_session, date(2026, 12, 29))

        assert self._entries(result)["Jan User"]["date"] == date(2027, 1, 5)

    def test_anniversary_outside_the_window_is_not_listed(self, db_session):
        _seed_base(db_session)
        _make_member(
            db_session,
            couleurname=None,
            vorname="Spaet",
            geburtsdatum=date(1990, 1, 20),
            geburtsdatum_accuracy=3,
        )

        assert compute_anniversaries(db_session, date(2026, 12, 29)) == {}

    def test_first_and_last_day_of_the_window_are_included(self, db_session):
        _seed_base(db_session)
        for first_name, birth in (
            ("Vortag", date(1990, 7, 19)),
            ("Montag", date(1990, 7, 20)),
            ("Sonntag", date(1990, 7, 26)),
            ("Folgetag", date(1990, 7, 27)),
        ):
            _make_member(
                db_session,
                email=f"{first_name}@vbw.at",
                couleurname=None,
                vorname=first_name,
                geburtsdatum=birth,
                geburtsdatum_accuracy=3,
            )

        result = compute_anniversaries(db_session, date(2026, 7, 14))

        assert sorted(self._entries(result)) == ["Montag User", "Sonntag User"]

    def test_ordinary_week_inside_a_year_is_unchanged(self, db_session):
        _seed_base(db_session)
        _make_member(
            db_session,
            couleurname=None,
            vorname="Juli",
            geburtsdatum=date(1990, 7, 22),
            geburtsdatum_accuracy=3,
        )

        result = compute_anniversaries(db_session, date(2026, 7, 14))

        entry = self._entries(result)["Juli User"]
        assert (entry["date"], entry["years"]) == (date(2026, 7, 22), 36)

    def test_leap_day_birthday_falls_back_to_the_28th(self, db_session):
        _seed_base(db_session)
        _make_member(
            db_session,
            couleurname=None,
            vorname="Schalt",
            geburtsdatum=date(2000, 2, 29),
            geburtsdatum_accuracy=3,
        )

        result = compute_anniversaries(db_session, date(2027, 2, 16))

        entry = self._entries(result)["Schalt User"]
        assert entry["date"] == date(2027, 2, 28)
        assert entry["leap_day_note"] is not None


class TestNewRules:
    def test_death_anniversary_of_a_deceased_member(self, db_session):
        _seed_base(db_session)
        m = _make_member(
            db_session,
            verstorben=True,
            sterbedatum=date(2001, 7, 21),
            sterbedatum_accuracy=3,
        )

        entry = compute_anniversaries(db_session, date(2026, 7, 14))["vbw"][
            "sterbedatum"
        ][0]

        assert (entry["cn"], entry["years"], entry["deceased"]) == (m.cn, 25, True)

    @pytest.mark.parametrize(("verstorben", "accuracy"), [(False, 3), (True, 2)])
    def test_no_death_anniversary_without_the_flag_or_an_exact_date(
        self, db_session, verstorben, accuracy
    ):
        _seed_base(db_session)
        _make_member(
            db_session,
            verstorben=verstorben,
            sterbedatum=date(2001, 7, 21),
            sterbedatum_accuracy=accuracy,
        )

        assert compute_anniversaries(db_session, date(2026, 7, 14)) == {}

    def test_a_date_in_the_coming_week_has_no_anniversary_yet(self, db_session):
        _seed_base(db_session)
        _make_member(
            db_session, aufnahmedatum=date(2026, 7, 21), aufnahmedatum_accuracy=3
        )

        assert compute_anniversaries(db_session, date(2026, 7, 14)) == {}

    def test_the_first_anniversary_is_listed(self, db_session):
        _seed_base(db_session)
        _make_member(
            db_session, aufnahmedatum=date(2025, 7, 21), aufnahmedatum_accuracy=3
        )

        entry = compute_anniversaries(db_session, date(2026, 7, 14))["vbw"][
            "aufnahmedatum"
        ][0]

        assert entry["years"] == 1

    def test_entries_sorted_by_date_then_name(self, db_session):
        _seed_base(db_session)
        for first_name, birth in (
            ("Cesar", date(1990, 7, 22)),
            ("Bert", date(1980, 7, 20)),
            ("Anna", date(1970, 7, 22)),
        ):
            _make_member(
                db_session,
                email=f"{first_name}@vbw.at",
                couleurname=None,
                vorname=first_name,
                geburtsdatum=birth,
                geburtsdatum_accuracy=3,
            )

        entries = compute_anniversaries(db_session, date(2026, 7, 14))["vbw"][
            "geburtsdatum"
        ]

        assert [e["cn"] for e in entries] == ["Bert User", "Anna User", "Cesar User"]

    def test_query_count_does_not_grow_with_members(self, db_session, count_queries):
        _seed_base(db_session)
        _make_member(
            db_session, geburtsdatum=date(1990, 7, 22), geburtsdatum_accuracy=3
        )
        with count_queries() as few:
            compute_anniversaries(db_session, date(2026, 7, 14))

        for i in range(10):
            _make_member(
                db_session,
                email=f"m{i}@vbw.at",
                couleurname=f"C{i}",
                geburtsdatum=date(1990, 7, 22),
                geburtsdatum_accuracy=3,
                aufnahmedatum=date(2010, 7, 23),
                aufnahmedatum_accuracy=3,
            )
        with count_queries() as many:
            compute_anniversaries(db_session, date(2026, 7, 14))

        # One SELECT per kind of anniversary, plus the savepoint the test
        # session opens around them.
        assert many.count == few.count == len(ANNIVERSARY_FIELDS) + 1


def _render(anniversaries, given=date(2027, 2, 16)) -> str:
    return render_chronicles(anniversaries, given)


def _entry(cn: str, years: int, *, deceased: bool = False, note: str | None = None):
    return {
        "cn": cn,
        "deceased": deceased,
        "date": date(2027, 2, 22),
        "weekday": "Mo",
        "day": "22.2.",
        "years": years,
        "leap_day_note": note,
    }


class TestRenderChronicles:
    def test_head_and_period(self):
        html = _render({})

        assert '<html lang="de">' in html
        assert '<meta charset="utf-8">' in html
        assert "22. Februar bis 28. Februar 2027" in html

    def test_an_empty_week_says_so(self):
        html = _render({})

        assert "stehen keine Jahrestage an" in html
        assert "Keine Jahrestage vom 22. Februar" in html

    def test_a_full_week_has_no_empty_week_text(self):
        html = _render({"vbw": {"geburtsdatum": [_entry("A v/o B", 37)]}})

        assert "stehen keine Jahrestage an" not in html
        assert "K.Ö.St.V. Vindobona II" in html
        assert "Vindobona nova" not in html

    def test_living_and_deceased_birthdays_of_both_orgs(self):
        html = _render(
            {
                "vbw": {
                    "geburtsdatum": [
                        _entry("Max Muster v/o Fix", 37),
                        _entry("Hans Alt wl. Eiche", 87, deceased=True),
                    ]
                },
                "vbn": {
                    "geburtsdatum": [
                        _entry("Eva Neu v/o Stern", 31),
                        _entry("Maria Alt wl. Aurora", 88, deceased=True),
                    ]
                },
            }
        )

        assert "Bbr. Max Muster v/o Fix" in html
        assert "feiert seinen 37. Geburtstag" in html
        assert "&dagger; Bbr. Hans Alt wl. Eiche" in html
        assert "hätte seinen 87. Geburtstag begangen" in html
        assert "Bsr. Eva Neu v/o Stern" in html
        assert "feiert ihren 31. Geburtstag" in html
        assert "hätte ihren 88. Geburtstag begangen" in html

    @pytest.mark.parametrize(
        ("org", "field", "text"),
        [
            ("vbw", "aufnahmedatum", "23. Jahrestag der Aufnahme"),
            ("vbw", "burschungsdatum", "23. Jahrestag der Burschung"),
            ("vbn", "burschungsdatum", "23. Jahrestag der Filiierung"),
            ("vbw", "philistrierungsdatum", "23. Jahrestag der Philistrierung"),
            ("vbw", "sterbedatum", "23. Todestag"),
        ],
    )
    def test_wording_of_each_kind(self, org, field, text):
        html = _render({org: {field: [_entry("A v/o B", 23)]}})

        assert text in html

    @pytest.mark.parametrize(
        ("field", "heading"),
        [
            ("geburtsdatum", "Geburtstage"),
            ("aufnahmedatum", "Aufnahmen"),
            ("philistrierungsdatum", "Philistrierungen"),
            ("sterbedatum", "Todestage"),
        ],
    )
    def test_section_headings(self, field, heading):
        assert f">{heading}</td>" in _render({"vbw": {field: [_entry("A", 3)]}})

    def test_burschung_heading_per_org(self):
        html = _render(
            {
                "vbw": {"burschungsdatum": [_entry("A", 3)]},
                "vbn": {"burschungsdatum": [_entry("B", 3)]},
            }
        )

        assert ">Burschungen</td>" in html
        assert ">Filiierungen</td>" in html

    def test_only_multiples_of_five_are_highlighted(self):
        html = _render(
            {
                "vbw": {
                    "aufnahmedatum": [_entry("A", 25), _entry("B", 24)],
                }
            }
        )

        assert "background-color:#F2E69C" in html
        assert 'border-radius:10px;">25.</span>' in html
        assert ">24.</span>" not in html
        assert "24. Jahrestag der Aufnahme" in html

    def test_leap_day_note_is_shown(self):
        html = _render(
            {"vbw": {"geburtsdatum": [_entry("A", 3, note="Hinweis zum Schalttag")]}}
        )

        assert "Hinweis zum Schalttag" in html

    def test_names_are_escaped(self):
        html = _render({"vbw": {"geburtsdatum": [_entry("<b>Böse</b>", 3)]}})

        assert "&lt;b&gt;Böse&lt;/b&gt;" in html
        assert "<b>Böse</b>" not in html
