"""Proves the FK ondelete/onupdate constraints (Alembic revision
3f58e5fc7f5f) are actually enforced by the database — the reason the whole
test suite was moved off SQLite onto real PostgreSQL. SQLite never enforces
foreign keys by default, so none of this was ever exercised before."""

import datetime

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.models.contact import Contact
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.personal_access_token import PersonalAccessToken
from app.models.role import Role
from app.models.standesdb_image import StandesdbImage


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


class TestPersonalAccessTokenFk:
    """personal_access_tokens.member_id -> members.id (Alembic revision
    5292367fb696) replaces the previously vestigial tokenable_type/
    tokenable_id pair — no code ever branched on the discriminator, so this
    became a real FK instead of an exclusive-arc redesign."""

    def test_inserting_a_token_with_unknown_member_id_is_rejected(self, db_session):
        db_session.add(
            PersonalAccessToken(member_id=999999, name="session", token="jti-1")
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_deleting_a_member_cascades_to_their_sessions(self, db_session):
        member = Member(vorname="Test", nachname="User")
        db_session.add(member)
        db_session.commit()

        token = PersonalAccessToken(member_id=member.id, name="session", token="jti-2")
        db_session.add(token)
        db_session.commit()
        token_id = token.id

        db_session.execute(delete(Member).where(Member.id == member.id))
        db_session.commit()

        assert db_session.get(PersonalAccessToken, token_id) is None


class TestStandesdbImageExclusiveArc:
    """standesdb_images.owner_member_id/owner_contact_id (Alembic revision
    1e14a4e8ec0c) replaces owner_type/owner_id with an exclusive-arc pair of
    real FKs — exactly one must be set, enforced by a CHECK constraint."""

    def test_both_owner_columns_set_is_rejected(self, db_session):
        member = Member(vorname="Test", nachname="User")
        contact = Contact(kontakttyp="person", name="Test Contact")
        db_session.add_all([member, contact])
        db_session.commit()

        db_session.add(
            StandesdbImage(
                owner_member_id=member.id,
                owner_contact_id=contact.id,
                sha256_hash="a" * 64,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_neither_owner_column_set_is_rejected(self, db_session):
        db_session.add(StandesdbImage(sha256_hash="b" * 64))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_inserting_with_unknown_owner_member_id_is_rejected(self, db_session):
        db_session.add(StandesdbImage(owner_member_id=999999, sha256_hash="c" * 64))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_inserting_with_unknown_owner_contact_id_is_rejected(self, db_session):
        db_session.add(StandesdbImage(owner_contact_id=999999, sha256_hash="d" * 64))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_deleting_a_member_cascades_to_their_images(self, db_session):
        member = Member(vorname="Test", nachname="User")
        db_session.add(member)
        db_session.commit()

        img = StandesdbImage(owner_member_id=member.id, sha256_hash="e" * 64)
        db_session.add(img)
        db_session.commit()
        img_id = img.id

        db_session.execute(delete(Member).where(Member.id == member.id))
        db_session.commit()

        assert db_session.get(StandesdbImage, img_id) is None

    def test_deleting_a_contact_cascades_to_their_images(self, db_session):
        contact = Contact(kontakttyp="person", name="Test Contact")
        db_session.add(contact)
        db_session.commit()

        img = StandesdbImage(owner_contact_id=contact.id, sha256_hash="f" * 64)
        db_session.add(img)
        db_session.commit()
        img_id = img.id

        db_session.execute(delete(Contact).where(Contact.id == contact.id))
        db_session.commit()

        assert db_session.get(StandesdbImage, img_id) is None
