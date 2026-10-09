"""Tests for the role-history overlap check.

Base rule: a role may be held by only one member at a time within an
organization.

Covered:
- Helper function _ranges_overlap (all 4 cases)
- Date order (startdate < enddate)
- Intra-request overlaps (same role, same member, overlapping periods)
- Database overlaps (same role, different member, same org)
- No error for: different roles, different orgs, non-overlapping periods
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


# ─── _ranges_overlap: helper function ───────────────────


class TestRangesOverlap:
    """All 4 cases of the overlap logic."""

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
        """Directly adjacent = no overlap (end == start)."""
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


# ─── Date order ──────────────────────────────────────────


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


# ─── Intra-request overlap ───────────────────────────────


class TestIntraRequestOverlap:
    """Same role, same member, overlapping periods within the request."""

    def test_same_role_overlapping_periods_rejected(self, db_session):
        """Case 1: both have an end date, periods overlap."""
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
        """Case 2/3: one has an end date, the other is ongoing, they overlap."""
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
        """Case 4: both without an end date = always overlapping."""
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
        """Same role, but periods are only adjacent."""
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
        """Different roles may share the same period."""
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
        """Three entries of the same role, only the 2nd and 3rd overlap."""
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


# ─── Database overlap ────────────────────────────────────


class TestDatabaseOverlap:
    """Same role, different member in the DB, same org."""

    def test_other_member_same_role_same_period_rejected(self, db_session):
        """A different member holds the role in the same period."""
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
        """A different member has an ongoing role, the new entry overlaps."""
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
        """A different member has an ongoing role, the new one is also ongoing."""
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
        """A different member held the role, but in a different period."""
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
        """A different member has a different role in the same period - no conflict."""
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
        """Same role, same period, different org -- no conflict."""
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
        """The member's own existing roles are ignored (detach-all + re-attach)."""
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


# ─── Empty and valid inputs ──────────────────────────────


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
        """Error message contains the role name."""
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
        """Label from the request is used in the error message."""
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
        """For a new member (member_id=None), no entry is ignored."""
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
