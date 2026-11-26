"""Readable old and new values for the change mail of a directory entry."""

import uuid
from datetime import date

import pytest

from app.models.badge import Badge
from app.models.contact import Contact
from app.models.key import Key
from app.models.member import Member
from app.models.role import Role
from app.services.change_description_service import describe_changes


def _member(**accuracies: int) -> Member:
    """A saved member as describe_changes sees it: the current values."""
    return Member(vorname="Max", nachname="Muster", **accuracies)


class TestDates:
    def test_only_the_date_changes_at_unchanged_accuracy(self, db_session):
        diff = {"geburtsdatum": {"old": date(1990, 5, 3), "new": date(1990, 6, 12)}}

        changes = describe_changes(db_session, diff, _member(geburtsdatum_accuracy=3))

        assert changes == {
            "geburtsdatum": {"old": "3. Mai 1990", "new": "12. Juni 1990"}
        }

    def test_only_the_accuracy_changes(self, db_session):
        diff = {"geburtsdatum_accuracy": {"old": 1, "new": 3}}
        member = _member(geburtsdatum_accuracy=3)
        member.geburtsdatum = date(1990, 5, 3)

        changes = describe_changes(db_session, diff, member)

        assert changes == {"geburtsdatum": {"old": "1990", "new": "3. Mai 1990"}}

    def test_old_and_new_each_at_their_own_accuracy(self, db_session):
        diff = {
            "aufnahmedatum": {"old": date(1990, 1, 1), "new": date(1990, 10, 7)},
            "aufnahmedatum_accuracy": {"old": 1, "new": 3},
        }

        changes = describe_changes(db_session, diff, _member(aufnahmedatum_accuracy=3))

        assert changes == {"aufnahmedatum": {"old": "1990", "new": "7. Oktober 1990"}}

    def test_an_unknown_date_reads_unknown(self, db_session):
        diff = {
            "sterbedatum": {"old": None, "new": date(2020, 5, 1)},
            "sterbedatum_accuracy": {"old": 0, "new": 2},
        }

        changes = describe_changes(db_session, diff, _member(sterbedatum_accuracy=2))

        assert changes == {"sterbedatum": {"old": "unbekannt", "new": "Mai 2020"}}

    def test_a_change_that_reads_the_same_is_left_out(self, db_session):
        diff = {
            "geburtsdatum": {"old": date(1990, 5, 3), "new": date(1990, 6, 12)},
            "vorname": {"old": "Max", "new": "Moritz"},
        }

        changes = describe_changes(db_session, diff, _member(geburtsdatum_accuracy=1))

        assert list(changes) == ["vorname"]

    def test_an_iso_string_counts_as_a_date(self, db_session):
        diff = {"datum": {"old": "", "new": "2021-03-04"}}
        contact = Contact(name="Firma", datum_accuracy=3)

        changes = describe_changes(db_session, diff, contact)

        assert changes == {"datum": {"old": "unbekannt", "new": "4. März 2021"}}


class TestPlainFields:
    @pytest.mark.parametrize(
        ("old", "new", "expected"),
        [
            (None, "Neu", {"old": "-", "new": "Neu"}),
            ("", "Neu", {"old": "-", "new": "Neu"}),
            (False, True, {"old": "False", "new": "True"}),
        ],
    )
    def test_values_as_text(self, db_session, old, new, expected):
        changes = describe_changes(
            db_session, {"vorname": {"old": old, "new": new}}, _member()
        )

        assert changes == {"vorname": expected}

    def test_the_order_of_the_diff_is_kept(self, db_session):
        diff = {
            "nachname": {"old": "A", "new": "B"},
            "geburtsdatum_accuracy": {"old": 0, "new": 1},
            "vorname": {"old": "C", "new": "D"},
        }
        member = _member(geburtsdatum_accuracy=1)
        member.geburtsdatum = date(1990, 5, 3)

        assert list(describe_changes(db_session, diff, member)) == [
            "nachname",
            "geburtsdatum",
            "vorname",
        ]


def _badge(db, name: str) -> uuid.UUID:
    badge = Badge(name=name, group="jubelband", sort_order=1)
    db.add(badge)
    db.commit()
    return badge.id


class TestBadgesKeysAndRoles:
    def test_badges_by_name_with_their_known_date(self, db_session):
        fux = _badge(db_session, "Fuxenband")
        zehn = _badge(db_session, "10-Semester-Band")
        diff = {
            "badges": {
                "old": [
                    {
                        "id": fux,
                        "presentationdate": "2020-01-01",
                        "presentationdate_accuracy": 1,
                    }
                ],
                "new": [
                    {
                        "id": fux,
                        "presentationdate": "2020-05-17",
                        "presentationdate_accuracy": 3,
                    },
                    {
                        "id": zehn,
                        "presentationdate": None,
                        "presentationdate_accuracy": 0,
                    },
                ],
            }
        }

        changes = describe_changes(db_session, diff, _member())

        assert changes == {
            "badges": {
                "old": "Fuxenband (2020)",
                "new": "Fuxenband (17. Mai 2020); 10-Semester-Band",
            }
        }

    def test_a_date_at_unknown_accuracy_shows_the_name_only(self, db_session):
        """Entries saved before dates were kept consistent can hold a date
        at accuracy "unknown"."""
        fux = _badge(db_session, "Fuxenband")
        diff = {
            "badges": {
                "old": [
                    {
                        "id": fux,
                        "presentationdate": "2020-01-01",
                        "presentationdate_accuracy": 0,
                    }
                ],
                "new": [
                    {
                        "id": fux,
                        "presentationdate": "2020-05-17",
                        "presentationdate_accuracy": 3,
                    }
                ],
            }
        }

        changes = describe_changes(db_session, diff, _member())

        assert changes == {
            "badges": {"old": "Fuxenband", "new": "Fuxenband (17. Mai 2020)"}
        }

    def test_an_emptied_list_and_a_vanished_badge(self, db_session):
        gone = uuid.uuid4()
        diff = {
            "badges": {
                "old": [
                    {
                        "id": gone,
                        "presentationdate": None,
                        "presentationdate_accuracy": 0,
                    }
                ],
                "new": [],
            }
        }

        changes = describe_changes(db_session, diff, _member())

        assert changes == {"badges": {"old": str(gone), "new": "-"}}

    def test_keys_by_name(self, db_session):
        key = Key(name="Haustorschlüssel")
        db_session.add(key)
        db_session.commit()
        diff = {
            "keys": {
                "old": [],
                "new": [
                    {
                        "id": key.id,
                        "presentationdate": "2019-09-01",
                        "presentationdate_accuracy": 2,
                    }
                ],
            }
        }

        changes = describe_changes(db_session, diff, _member())

        assert changes == {
            "keys": {"old": "-", "new": "Haustorschlüssel (September 2019)"}
        }

    def test_roles_by_label_with_the_time_held(self, db_session):
        db_session.add_all(
            [
                Role(id="senior", group="chc", label="Senior", sort_order=1),
                Role(id="fm", group="chc", label=None, sort_order=2),
            ]
        )
        db_session.commit()
        diff = {
            "roles_history": {
                "old": [{"id": "senior", "startdate": "2020-02-01", "enddate": None}],
                "new": [
                    {
                        "id": "senior",
                        "startdate": "2020-02-01",
                        "enddate": "2020-07-31",
                    },
                    {"id": "fm", "startdate": "2021-03-01", "enddate": None},
                ],
            }
        }

        changes = describe_changes(db_session, diff, _member())

        assert changes == {
            "roles_history": {
                "old": "Senior (seit 1. Februar 2020)",
                "new": (
                    "Senior (1. Februar 2020 bis 31. Juli 2020); fm (seit 1. März 2021)"
                ),
            }
        }

    def test_one_name_query_per_list_whatever_its_length(
        self, db_session, count_queries
    ):
        ids = [_badge(db_session, f"Band {i}") for i in range(6)]
        key = Key(name="Schlüssel")
        db_session.add_all(
            [key, Role(id="senior", group="chc", label="Senior", sort_order=1)]
        )
        db_session.commit()

        def diff(n: int) -> dict[str, dict[str, object]]:
            return {
                "badges": {
                    "old": [],
                    "new": [
                        {
                            "id": i,
                            "presentationdate": None,
                            "presentationdate_accuracy": 0,
                        }
                        for i in ids[:n]
                    ],
                },
                "keys": {
                    "old": [],
                    "new": [
                        {
                            "id": key.id,
                            "presentationdate": None,
                            "presentationdate_accuracy": 0,
                        }
                    ]
                    * n,
                },
                "roles_history": {
                    "old": [],
                    "new": [
                        {"id": "senior", "startdate": "2020-02-01", "enddate": None}
                    ]
                    * n,
                },
            }

        one, six = diff(1), diff(6)
        with count_queries() as few:
            describe_changes(db_session, one, _member())
        with count_queries() as many:
            describe_changes(db_session, six, _member())

        # One name query each for badges, keys and roles.
        assert many.count == few.count == 3
