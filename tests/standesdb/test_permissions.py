"""Tests für calculate_permissions().

Ableitung der Berechtigungen aus aktiven Rollen.
"""

from datetime import UTC, date, datetime, timedelta

from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services.permission_service import calculate_permissions


def _seed(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Org(id="vbn", label="VBN", order=2),
            State(id="fu", label="Fux", order=1),
            Role(id="standesfuehrer", group="chc", label="Standesführer", order=1),
            Role(
                id="internetreferent",
                group="funktion",
                label="Internetreferent",
                order=2,
            ),
            Role(id="senior", group="chc", label="Senior", order=3),
            Role(id="phil-senior", group="philchc", label="Phil-Senior", order=4),
            Role(id="phil-xxxx", group="philchc", label="Phil-x", order=5),
            Role(id="fuchsmajor", group="chc", label="Fuchsmajor", order=6),
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


class TestCalculatePermissions:
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

    def test_internetreferent_vbn_no_archive(self, db_session):
        _seed(db_session)
        m = _member(db_session, "vbn")
        _assign(db_session, m.id, "internetreferent")
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
        yesterday = datetime.now(UTC).date() - timedelta(days=1)
        _assign(db_session, m.id, "senior", date(2020, 1, 1), yesterday)
        perms = calculate_permissions(m)
        assert "keylist" not in perms
        assert "standesdbExport" not in perms

    def test_future_role_no_permissions(self, db_session):
        _seed(db_session)
        m = _member(db_session)
        tomorrow = datetime.now(UTC).date() + timedelta(days=1)
        _assign(db_session, m.id, "senior", tomorrow, None)
        perms = calculate_permissions(m)
        assert perms == []

    def test_ongoing_role_has_permissions(self, db_session):
        _seed(db_session)
        m = _member(db_session)
        _assign(db_session, m.id, "senior", date(2020, 1, 1), None)
        perms = calculate_permissions(m)
        assert "keylist" in perms
