"""Tests für die Rollenhistorie-Überlappungsprüfung.

Grundregel: Eine Rolle darf zu jedem Zeitpunkt innerhalb einer Organisation
nur von einem Mitglied besetzt sein.

Geprüft werden:
- Hilfsfunktion _ranges_overlap (alle 4 Fälle)
- Datumsreihenfolge (startdate < enddate)
- Intra-Request-Überlappungen (gleiche Rolle, gleiches Mitglied, überlappende Zeiträume)
- Datenbank-Überlappungen (gleiche Rolle, anderes Mitglied, gleiche Org)
- Kein Fehler bei: verschiedenen Rollen, verschiedenen Orgs,
  nicht-überlappenden Zeiträumen
"""

from datetime import date

import pytest
from fastapi import HTTPException

from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services.standesdb_service import (
    _ranges_overlap,
    validate_roles_history,
)

# ─── Fixtures ───────────────────────────────────────────


def _seed(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Org(id="vbn", label="VBN", order=2),
            State(id="fu", label="Fux", order=1),
            Role(id="senior", group="chc", label="Senior", order=1),
            Role(id="schriftfuehrer", group="chc", label="Schriftführer", order=2),
            Role(id="fuchsmajor", group="chc", label="Fuchsmajor", order=3),
        ]
    )
    db.commit()


def _member(db, *, email, org_id="vbw", vorname="Test", nachname="User"):
    m = Member(
        email=email,
        org_id=org_id,
        vorname=vorname,
        nachname=nachname,
        auth_locked=True,
    )
    db.add(m)
    db.commit()
    return m


# ─── _ranges_overlap: Hilfsfunktion ────────────────────


class TestRangesOverlap:
    """Alle 4 Fälle der Überlappungslogik."""

    def test_both_have_enddate_overlap(self):
        assert (
            _ranges_overlap(
                date(1997, 2, 1),
                date(1997, 7, 31),
                date(1997, 5, 1),
                date(1998, 1, 31),
            )
            is True
        )

    def test_both_have_enddate_no_overlap(self):
        assert (
            _ranges_overlap(
                date(1997, 2, 1),
                date(1997, 7, 31),
                date(1997, 8, 1),
                date(1998, 1, 31),
            )
            is False
        )

    def test_both_have_enddate_adjacent_no_overlap(self):
        """Direkt anschließend = keine Überlappung (end == start)."""
        assert (
            _ranges_overlap(
                date(1997, 2, 1),
                date(1997, 8, 1),
                date(1997, 8, 1),
                date(1998, 1, 31),
            )
            is False
        )

    def test_first_has_enddate_second_ongoing_overlap(self):
        assert (
            _ranges_overlap(
                date(1997, 2, 1),
                date(1997, 7, 31),
                date(1997, 5, 1),
                None,
            )
            is True
        )

    def test_first_has_enddate_second_ongoing_no_overlap(self):
        assert (
            _ranges_overlap(
                date(1997, 2, 1),
                date(1997, 7, 31),
                date(1997, 8, 1),
                None,
            )
            is False
        )

    def test_first_ongoing_second_has_enddate_overlap(self):
        assert (
            _ranges_overlap(
                date(1997, 5, 1),
                None,
                date(1997, 2, 1),
                date(1997, 7, 31),
            )
            is True
        )

    def test_first_ongoing_second_has_enddate_no_overlap(self):
        assert (
            _ranges_overlap(
                date(1997, 8, 1),
                None,
                date(1997, 2, 1),
                date(1997, 7, 31),
            )
            is False
        )

    def test_both_ongoing_always_overlap(self):
        assert (
            _ranges_overlap(
                date(1997, 2, 1),
                None,
                date(2020, 1, 1),
                None,
            )
            is True
        )

    def test_identical_ranges_overlap(self):
        assert (
            _ranges_overlap(
                date(1997, 2, 1),
                date(1997, 7, 31),
                date(1997, 2, 1),
                date(1997, 7, 31),
            )
            is True
        )


# ─── Datumsreihenfolge ─────────────────────────────────


class TestDateOrder:
    def test_startdate_after_enddate_rejected(self, db_session):
        _seed(db_session)
        roles = [
            {
                "id": "senior",
                "startdate": date(1998, 1, 31),
                "enddate": date(1997, 2, 1),
            },
        ]
        with pytest.raises(HTTPException) as exc:
            validate_roles_history(db_session, roles, "vbw", None)
        assert exc.value.status_code == 422
        assert "muss vor Enddatum" in exc.value.detail[0]

    def test_startdate_equals_enddate_rejected(self, db_session):
        _seed(db_session)
        roles = [
            {
                "id": "senior",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 2, 1),
            },
        ]
        with pytest.raises(HTTPException) as exc:
            validate_roles_history(db_session, roles, "vbw", None)
        assert exc.value.status_code == 422

    def test_startdate_before_enddate_ok(self, db_session):
        _seed(db_session)
        roles = [
            {
                "id": "senior",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 7, 31),
            },
        ]
        validate_roles_history(db_session, roles, "vbw", None)

    def test_no_enddate_ok(self, db_session):
        _seed(db_session)
        roles = [
            {"id": "senior", "startdate": date(1997, 2, 1)},
        ]
        validate_roles_history(db_session, roles, "vbw", None)


# ─── Intra-Request-Überlappung ─────────────────────────


class TestIntraRequestOverlap:
    """Gleiche Rolle, gleiches Mitglied, überlappende Zeiträume im Request."""

    def test_same_role_overlapping_periods_rejected(self, db_session):
        """Fall 1: Beide haben Enddatum, Zeiträume überlappen."""
        _seed(db_session)
        roles = [
            {
                "id": "senior",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 7, 31),
            },
            {
                "id": "senior",
                "startdate": date(1997, 5, 1),
                "enddate": date(1998, 1, 31),
            },
        ]
        with pytest.raises(HTTPException) as exc:
            validate_roles_history(db_session, roles, "vbw", None)
        assert exc.value.status_code == 422
        assert "überschneiden sich" in exc.value.detail[0]

    def test_same_role_one_ongoing_overlap_rejected(self, db_session):
        """Fall 2/3: Einer hat Enddatum, anderer laufend, überlappen."""
        _seed(db_session)
        roles = [
            {
                "id": "senior",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 7, 31),
            },
            {"id": "senior", "startdate": date(1997, 5, 1)},
        ]
        with pytest.raises(HTTPException) as exc:
            validate_roles_history(db_session, roles, "vbw", None)
        assert exc.value.status_code == 422
        assert "überschneiden sich" in exc.value.detail[0]

    def test_same_role_both_ongoing_rejected(self, db_session):
        """Fall 4: Beide ohne Enddatum = immer Überlappung."""
        _seed(db_session)
        roles = [
            {"id": "senior", "startdate": date(1997, 2, 1)},
            {"id": "senior", "startdate": date(2020, 1, 1)},
        ]
        with pytest.raises(HTTPException) as exc:
            validate_roles_history(db_session, roles, "vbw", None)
        assert exc.value.status_code == 422
        assert "überschneiden sich" in exc.value.detail[0]

    def test_same_role_non_overlapping_ok(self, db_session):
        """Gleiche Rolle, aber Zeiträume grenzen nur aneinander."""
        _seed(db_session)
        roles = [
            {
                "id": "senior",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 7, 31),
            },
            {
                "id": "senior",
                "startdate": date(1997, 8, 1),
                "enddate": date(1998, 1, 31),
            },
        ]
        validate_roles_history(db_session, roles, "vbw", None)

    def test_different_roles_same_period_ok(self, db_session):
        """Verschiedene Rollen dürfen im gleichen Zeitraum liegen."""
        _seed(db_session)
        roles = [
            {
                "id": "senior",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 7, 31),
            },
            {
                "id": "schriftfuehrer",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 7, 31),
            },
        ]
        validate_roles_history(db_session, roles, "vbw", None)

    def test_three_entries_third_overlaps(self, db_session):
        """Drei Einträge gleicher Rolle, nur 2. und 3. überlappen."""
        _seed(db_session)
        roles = [
            {
                "id": "senior",
                "startdate": date(1995, 2, 1),
                "enddate": date(1995, 7, 31),
            },
            {
                "id": "senior",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 7, 31),
            },
            {
                "id": "senior",
                "startdate": date(1997, 5, 1),
                "enddate": date(1998, 1, 31),
            },
        ]
        with pytest.raises(HTTPException) as exc:
            validate_roles_history(db_session, roles, "vbw", None)
        assert exc.value.status_code == 422


# ─── Datenbank-Überlappung ─────────────────────────────


class TestDatabaseOverlap:
    """Gleiche Rolle, anderes Mitglied in der DB, gleiche Org."""

    def test_other_member_same_role_same_period_rejected(self, db_session):
        """Anderes Mitglied hält die Rolle im selben Zeitraum."""
        _seed(db_session)
        other = _member(
            db_session, email="other@vbw.at", vorname="Max", nachname="Muster"
        )
        me = _member(db_session, email="me@vbw.at", vorname="Ich", nachname="Selbst")

        db_session.add(
            MemberRole(
                member_id=other.id,
                role_id="senior",
                startdate=date(1997, 2, 1),
                enddate=date(1997, 7, 31),
            )
        )
        db_session.commit()

        roles = [
            {
                "id": "senior",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 7, 31),
            },
        ]
        with pytest.raises(HTTPException) as exc:
            validate_roles_history(db_session, roles, "vbw", me.id)
        assert exc.value.status_code == 422
        assert "Max Muster" in exc.value.detail[0]
        assert "belegt" in exc.value.detail[0]

    def test_other_member_ongoing_new_overlaps_rejected(self, db_session):
        """Anderes Mitglied hat laufende Rolle, neuer Eintrag überlappt."""
        _seed(db_session)
        other = _member(
            db_session, email="other@vbw.at", vorname="Max", nachname="Muster"
        )
        me = _member(db_session, email="me@vbw.at", vorname="Ich", nachname="Selbst")

        db_session.add(
            MemberRole(
                member_id=other.id,
                role_id="senior",
                startdate=date(2020, 1, 1),
                enddate=None,
            )
        )
        db_session.commit()

        roles = [
            {
                "id": "senior",
                "startdate": date(2023, 1, 1),
                "enddate": date(2023, 7, 31),
            },
        ]
        with pytest.raises(HTTPException) as exc:
            validate_roles_history(db_session, roles, "vbw", me.id)
        assert exc.value.status_code == 422

    def test_both_ongoing_rejected(self, db_session):
        """Anderes Mitglied hat laufende Rolle, neuer auch laufend."""
        _seed(db_session)
        other = _member(
            db_session, email="other@vbw.at", vorname="Max", nachname="Muster"
        )
        me = _member(db_session, email="me@vbw.at", vorname="Ich", nachname="Selbst")

        db_session.add(
            MemberRole(
                member_id=other.id,
                role_id="senior",
                startdate=date(2020, 1, 1),
                enddate=None,
            )
        )
        db_session.commit()

        roles = [
            {"id": "senior", "startdate": date(2023, 1, 1)},
        ]
        with pytest.raises(HTTPException) as exc:
            validate_roles_history(db_session, roles, "vbw", me.id)
        assert exc.value.status_code == 422

    def test_other_member_non_overlapping_ok(self, db_session):
        """Anderes Mitglied hielt die Rolle, aber in anderem Zeitraum."""
        _seed(db_session)
        other = _member(
            db_session, email="other@vbw.at", vorname="Max", nachname="Muster"
        )
        me = _member(db_session, email="me@vbw.at", vorname="Ich", nachname="Selbst")

        db_session.add(
            MemberRole(
                member_id=other.id,
                role_id="senior",
                startdate=date(1997, 2, 1),
                enddate=date(1997, 7, 31),
            )
        )
        db_session.commit()

        roles = [
            {
                "id": "senior",
                "startdate": date(1997, 8, 1),
                "enddate": date(1998, 1, 31),
            },
        ]
        validate_roles_history(db_session, roles, "vbw", me.id)

    def test_other_member_different_role_ok(self, db_session):
        """Anderes Mitglied hat andere Rolle im gleichen Zeitraum — kein Konflikt."""
        _seed(db_session)
        other = _member(
            db_session, email="other@vbw.at", vorname="Max", nachname="Muster"
        )
        me = _member(db_session, email="me@vbw.at", vorname="Ich", nachname="Selbst")

        db_session.add(
            MemberRole(
                member_id=other.id,
                role_id="schriftfuehrer",
                startdate=date(1997, 2, 1),
                enddate=date(1997, 7, 31),
            )
        )
        db_session.commit()

        roles = [
            {
                "id": "senior",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 7, 31),
            },
        ]
        validate_roles_history(db_session, roles, "vbw", me.id)

    def test_other_member_different_org_ok(self, db_session):
        """Gleiche Rolle, gleicher Zeitraum, andere Org -- kein Konflikt."""
        _seed(db_session)
        other_vbn = _member(
            db_session,
            email="other@vbn.at",
            org_id="vbn",
            vorname="Max",
            nachname="Muster",
        )
        me = _member(db_session, email="me@vbw.at", vorname="Ich", nachname="Selbst")

        db_session.add(
            MemberRole(
                member_id=other_vbn.id,
                role_id="senior",
                startdate=date(1997, 2, 1),
                enddate=date(1997, 7, 31),
            )
        )
        db_session.commit()

        roles = [
            {
                "id": "senior",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 7, 31),
            },
        ]
        validate_roles_history(db_session, roles, "vbw", me.id)

    def test_own_existing_roles_ignored(self, db_session):
        """Eigene bestehende Rollen werden ignoriert (detach-all + re-attach)."""
        _seed(db_session)
        me = _member(db_session, email="me@vbw.at", vorname="Ich", nachname="Selbst")

        db_session.add(
            MemberRole(
                member_id=me.id,
                role_id="senior",
                startdate=date(1997, 2, 1),
                enddate=date(1997, 7, 31),
            )
        )
        db_session.commit()

        roles = [
            {
                "id": "senior",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 7, 31),
            },
        ]
        validate_roles_history(db_session, roles, "vbw", me.id)


# ─── Leere und valide Eingaben ─────────────────────────


class TestEdgeCases:
    def test_empty_roles_ok(self, db_session):
        _seed(db_session)
        validate_roles_history(db_session, [], "vbw", None)

    def test_single_role_ok(self, db_session):
        _seed(db_session)
        roles = [
            {
                "id": "senior",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 7, 31),
            },
        ]
        validate_roles_history(db_session, roles, "vbw", None)

    def test_label_in_error_message(self, db_session):
        """Fehlermeldung enthält den Rollennamen."""
        _seed(db_session)
        roles = [
            {
                "id": "senior",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 7, 31),
            },
            {
                "id": "senior",
                "startdate": date(1997, 5, 1),
                "enddate": date(1998, 1, 31),
            },
        ]
        with pytest.raises(HTTPException) as exc:
            validate_roles_history(db_session, roles, "vbw", None)
        assert "Senior" in exc.value.detail[0]

    def test_label_from_request_used(self, db_session):
        """Label aus dem Request wird in der Fehlermeldung verwendet."""
        _seed(db_session)
        roles = [
            {
                "id": "senior",
                "label": "Senior (custom)",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 7, 31),
            },
            {
                "id": "senior",
                "label": "Senior (custom)",
                "startdate": date(1997, 5, 1),
                "enddate": date(1998, 1, 31),
            },
        ]
        with pytest.raises(HTTPException) as exc:
            validate_roles_history(db_session, roles, "vbw", None)
        assert "Senior (custom)" in exc.value.detail[0]

    def test_new_member_no_member_id(self, db_session):
        """Bei neuem Mitglied (member_id=None) wird kein Eintrag ignoriert."""
        _seed(db_session)
        existing = _member(
            db_session, email="ex@vbw.at", vorname="Max", nachname="Muster"
        )
        db_session.add(
            MemberRole(
                member_id=existing.id,
                role_id="senior",
                startdate=date(1997, 2, 1),
                enddate=date(1997, 7, 31),
            )
        )
        db_session.commit()

        roles = [
            {
                "id": "senior",
                "startdate": date(1997, 2, 1),
                "enddate": date(1997, 7, 31),
            },
        ]
        with pytest.raises(HTTPException) as exc:
            validate_roles_history(db_session, roles, "vbw", None)
        assert exc.value.status_code == 422
