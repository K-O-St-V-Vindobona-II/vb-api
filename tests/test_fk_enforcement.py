"""Proves the FK ondelete/onupdate constraints (Alembic revision
3f58e5fc7f5f) are actually enforced by the database — the reason the whole
test suite was moved off SQLite onto real PostgreSQL. SQLite never enforces
foreign keys by default, so none of this was ever exercised before."""

import datetime

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.role import Role


def _seed_member_with_role(db) -> tuple[Member, Role, MemberRole]:
    member = Member(vorname="Test", nachname="User")
    role = Role(id="fk-test-role", label="FK Test Role")
    db.add_all([member, role])
    db.commit()

    member_role = MemberRole(
        member_id=member.id,
        role_id=role.id,
        startdate=datetime.date(2020, 1, 1),
    )
    db.add(member_role)
    db.commit()
    return member, role, member_role


class TestFkOndeleteRestrict:
    def test_deleting_a_role_still_assigned_to_a_member_is_blocked(self, db_session):
        """members_roles.role_id -> roles.id is ON DELETE RESTRICT."""
        _member, role, _member_role = _seed_member_with_role(db_session)

        db_session.delete(role)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestFkOndeleteCascade:
    def test_deleting_a_member_cascades_to_their_role_assignments(self, db_session):
        """members_roles.member_id -> members.id is ON DELETE CASCADE."""
        member, _role, member_role = _seed_member_with_role(db_session)
        pk = (member_role.member_id, member_role.role_id, member_role.startdate)

        # A Core-level DELETE, not session.delete(member): the ORM's own
        # unit-of-work would otherwise try to null out members_roles.member_id
        # itself (as if onupdate were SET NULL) before the DB gets a chance to
        # apply the real ON DELETE CASCADE — and fail, since member_id is part
        # of that table's primary key. This way the DB constraint is what
        # actually gets exercised, not SQLAlchemy's in-memory cascade guess.
        db_session.execute(delete(Member).where(Member.id == member.id))
        db_session.commit()

        assert db_session.get(MemberRole, pk) is None


class TestFkInsertEnforcement:
    def test_inserting_a_member_role_with_unknown_role_id_is_rejected(self, db_session):
        member = Member(vorname="Test", nachname="User")
        db_session.add(member)
        db_session.commit()

        db_session.add(
            MemberRole(
                member_id=member.id,
                role_id="does-not-exist",
                startdate=datetime.date(2020, 1, 1),
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
