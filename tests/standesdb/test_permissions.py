"""Tests for calculate_permissions().

Derives the permission set from a member's active roles.
"""

from datetime import date, timedelta

import pytest

from app.core.datetime_utils import local_today
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services.permission_service import calculate_permissions


def _seed(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", sort_order=1),
            Org(id="vbn", label="VBN", sort_order=2),
            State(id="fu", label="Fux", sort_order=1),
            Role(id="standesfuehrer", group="chc", label="Standesführer", sort_order=1),
            Role(
                id="internetreferent",
                group="funktion",
                label="Internetreferent",
                sort_order=2,
            ),
            Role(id="senior", group="chc", label="Senior", sort_order=3),
            Role(id="phil-senior", group="philchc", label="Phil-Senior", sort_order=4),
            Role(id="phil-xxxx", group="philchc", label="Phil-x", sort_order=5),
            Role(id="fuchsmajor", group="chc", label="Fuchsmajor", sort_order=6),
            Role(id="archivar2", group="funktion", label="Archivar 2", sort_order=7),
        ]
    )
    db.commit()


def _member(db, org_id="vbw"):
    m = Member(email=f"test@{org_id}.at", org_id=org_id, vorname="T", nachname="U")
    db.add(m)
    db.commit()
    return m


def _assign(db, member_id, role_id, start=None, end=None):
    db.add(
        MemberRole(
            member_id=member_id,
            role_id=role_id,
            startdate=start or date(2020, 1, 1),
            enddate=end,
        )
    )
    db.commit()


# Exact permission set of a member who holds only this role, per organisation.
EXPECTED_PERMISSIONS = [
    (
        "standesfuehrer",
        "vbw",
        ["keylist", "standesdbContactAdmin", "standesdbExport", "standesdbVbwAdmin"],
    ),
    (
        "standesfuehrer",
        "vbn",
        ["keylist", "standesdbContactAdmin", "standesdbExport", "standesdbVbnAdmin"],
    ),
    (
        "internetreferent",
        "vbw",
        ["archiveAdmin", "publicContentEditor", "standesdbVbwAdmin", "systemAdmin"],
    ),
    ("internetreferent", "vbn", []),
    ("archivar2", "vbw", ["archiveAdmin"]),
    ("archivar2", "vbn", []),
    ("senior", "vbw", ["keylist", "standesdbExport"]),
    ("senior", "vbn", ["keylist", "standesdbExport"]),
    ("phil-senior", "vbw", ["keylist", "p4xView", "standesdbExport"]),
    ("phil-senior", "vbn", ["keylist", "standesdbExport"]),
    ("phil-xxxx", "vbw", ["keylist", "p4xAdmin", "p4xView", "standesdbExport"]),
    ("phil-xxxx", "vbn", ["keylist", "standesdbExport"]),
]


class TestCalculatePermissions:
    @pytest.mark.parametrize(("role", "org", "expected"), EXPECTED_PERMISSIONS)
    def test_a_role_confers_exactly_these_permissions(
        self, db_session, role, org, expected
    ):
        _seed(db_session)
        m = _member(db_session, org)
        _assign(db_session, m.id, role)

        assert sorted(calculate_permissions(m)) == expected

    def test_standesfuehrer_vbw(self, db_session):
        _seed(db_session)
        m = _member(db_session, "vbw")
        _assign(db_session, m.id, "standesfuehrer")
        perms = calculate_permissions(m)
        assert "standesdbVbwAdmin" in perms
        assert "standesdbContactAdmin" in perms
        assert "standesdbExport" in perms

    def test_standesfuehrer_vbn(self, db_session):
        _seed(db_session)
        m = _member(db_session, "vbn")
        _assign(db_session, m.id, "standesfuehrer")
        perms = calculate_permissions(m)
        assert "standesdbVbnAdmin" in perms
        assert "standesdbContactAdmin" in perms
        assert "standesdbVbwAdmin" not in perms

    def test_internetreferent_vbw(self, db_session):
        _seed(db_session)
        m = _member(db_session, "vbw")
        _assign(db_session, m.id, "internetreferent")
        perms = calculate_permissions(m)
        assert "archiveAdmin" in perms
        assert "systemAdmin" in perms
        assert "standesdbVbwAdmin" in perms

    def test_internetreferent_vbw_gets_no_other_standesdb_permissions(self, db_session):
        """The internet officer administers the VBW member database only; the
        contact, export and VBN permissions stay with the registrar role."""
        _seed(db_session)
        m = _member(db_session, "vbw")
        _assign(db_session, m.id, "internetreferent")
        perms = calculate_permissions(m)
        assert "standesdbVbnAdmin" not in perms
        assert "standesdbContactAdmin" not in perms
        assert "standesdbExport" not in perms

    def test_internetreferent_vbn_no_archive(self, db_session):
        _seed(db_session)
        m = _member(db_session, "vbn")
        _assign(db_session, m.id, "internetreferent")
        perms = calculate_permissions(m)
        assert "archiveAdmin" not in perms
        assert "standesdbVbwAdmin" not in perms

    def test_expired_internetreferent_vbw_loses_standesdb_admin(self, db_session):
        _seed(db_session)
        m = _member(db_session, "vbw")
        yesterday = local_today() - timedelta(days=1)
        _assign(db_session, m.id, "internetreferent", date(2020, 1, 1), yesterday)
        perms = calculate_permissions(m)
        assert "standesdbVbwAdmin" not in perms

    def test_archivar2_vbw(self, db_session):
        _seed(db_session)
        m = _member(db_session, "vbw")
        _assign(db_session, m.id, "archivar2")
        perms = calculate_permissions(m)
        assert "archiveAdmin" in perms

    def test_archivar2_vbn_no_archive(self, db_session):
        _seed(db_session)
        m = _member(db_session, "vbn")
        _assign(db_session, m.id, "archivar2")
        perms = calculate_permissions(m)
        assert "archiveAdmin" not in perms

    def test_chc_group_gets_export_and_keylist(self, db_session):
        _seed(db_session)
        m = _member(db_session)
        _assign(db_session, m.id, "senior")
        perms = calculate_permissions(m)
        assert "standesdbExport" in perms
        assert "keylist" in perms

    def test_philchc_gets_p4x_view(self, db_session):
        _seed(db_session)
        m = _member(db_session, "vbw")
        _assign(db_session, m.id, "phil-senior")
        perms = calculate_permissions(m)
        assert "p4xView" in perms

    def test_phil_xxxx_gets_p4x_admin(self, db_session):
        _seed(db_session)
        m = _member(db_session, "vbw")
        _assign(db_session, m.id, "phil-xxxx")
        perms = calculate_permissions(m)
        assert "p4xAdmin" in perms

    def test_no_roles_no_permissions(self, db_session):
        _seed(db_session)
        m = _member(db_session)
        perms = calculate_permissions(m)
        assert perms == []

    def test_expired_role_no_permissions(self, db_session):
        _seed(db_session)
        m = _member(db_session)
        yesterday = local_today() - timedelta(days=1)
        _assign(db_session, m.id, "senior", date(2020, 1, 1), yesterday)
        perms = calculate_permissions(m)
        assert "keylist" not in perms
        assert "standesdbExport" not in perms

    def test_future_role_no_permissions(self, db_session):
        _seed(db_session)
        m = _member(db_session)
        tomorrow = local_today() + timedelta(days=1)
        _assign(db_session, m.id, "senior", tomorrow, None)
        perms = calculate_permissions(m)
        assert perms == []

    def test_ongoing_role_has_permissions(self, db_session):
        _seed(db_session)
        m = _member(db_session)
        _assign(db_session, m.id, "senior", date(2020, 1, 1), None)
        perms = calculate_permissions(m)
        assert "keylist" in perms

    def test_role_boundary_uses_configured_app_timezone_not_utc(
        self, db_session, monkeypatch
    ):
        """Regression (2026-08-15 timezone audit): _active_roles() previously
        compared MemberRole.startdate/enddate against datetime.now(UTC).date().
        Role dates are Vienna-local calendar days entered by admins, not UTC
        ones -- a role starting "today" could be wrongly treated as not yet
        active for up to 2h after Vienna midnight, when UTC's date still lags
        a day behind. Patching local_today() directly (rather than the real
        system clock) keeps this deterministic regardless of when the suite
        runs."""
        fixed_today = date(2026, 6, 15)
        monkeypatch.setattr(
            "app.services.permission_service.local_today", lambda: fixed_today
        )
        _seed(db_session)
        m = _member(db_session)
        _assign(db_session, m.id, "senior", fixed_today, None)
        perms = calculate_permissions(m)
        assert "keylist" in perms
