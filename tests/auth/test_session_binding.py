"""A session belongs to the member it was created for, not to whoever holds
the e-mail address named in the token later."""

import uuid

import pytest
from fastapi import HTTPException

from app.api import deps
from app.models.auth_session import AuthSession
from app.models.member import Member
from app.services.auth_service import create_user_session

OLD_ADDRESS = "old.address@vindobona.at"
NEW_ADDRESS = "new.address@vindobona.at"


def _member(db, email: str, *, locked: bool = False) -> Member:
    member = Member(email=email, auth_locked=locked)
    db.add(member)
    db.commit()
    return member


def _get_me(client, token: str):
    return client.get("/api/members/me", headers={"Authorization": f"Bearer {token}"})


def _hand_over_the_address(db, original: Member, *, new_owner_locked: bool = False):
    """The original owner moves to another address, the old one goes to a
    different member."""
    original.email = NEW_ADDRESS
    db.commit()
    return _member(db, OLD_ADDRESS, locked=new_owner_locked)


class TestSessionIsBoundToMember:
    def test_the_owner_of_the_session_is_accepted(self, client, db_session):
        owner = _member(db_session, OLD_ADDRESS)
        token, _, _ = create_user_session(db_session, owner)

        response = _get_me(client, token)

        assert response.status_code == 200
        assert response.json()["email"] == OLD_ADDRESS

    def test_a_token_of_a_reassigned_address_is_rejected(self, client, db_session):
        original = _member(db_session, OLD_ADDRESS)
        token, _, _ = create_user_session(db_session, original)
        _hand_over_the_address(db_session, original)

        assert _get_me(client, token).status_code == 401

    def test_the_new_owner_is_not_told_anything_about_the_account(
        self, client, db_session
    ):
        original = _member(db_session, OLD_ADDRESS)
        token, _, _ = create_user_session(db_session, original)
        _hand_over_the_address(db_session, original, new_owner_locked=True)

        # A locked account would answer 403: the binding is checked first.
        assert _get_me(client, token).status_code == 401

    def test_a_changed_address_without_a_new_owner_is_still_rejected(
        self, client, db_session
    ):
        original = _member(db_session, OLD_ADDRESS)
        token, _, _ = create_user_session(db_session, original)
        original.email = NEW_ADDRESS
        db_session.commit()

        assert _get_me(client, token).status_code == 401

    def test_a_new_session_after_the_change_works_again(self, client, db_session):
        original = _member(db_session, OLD_ADDRESS)
        _hand_over_the_address(db_session, original)
        token, _, _ = create_user_session(db_session, original)

        response = _get_me(client, token)

        assert response.status_code == 200
        assert response.json()["email"] == NEW_ADDRESS

    def test_a_locked_owner_is_still_refused_with_403(self, client, db_session):
        owner = _member(db_session, OLD_ADDRESS)
        token, _, _ = create_user_session(db_session, owner)
        owner.auth_locked = True
        db_session.commit()

        assert _get_me(client, token).status_code == 403


class TestVerifiedUser:
    def test_a_session_of_another_member_is_refused(self, db_session):
        member = _member(db_session, OLD_ADDRESS)
        foreign_session = AuthSession(member_id=uuid.uuid4(), jti="foreign")

        with pytest.raises(HTTPException) as info:
            deps._get_verified_user(db_session, member.email, foreign_session)

        assert info.value.status_code == 401

    def test_the_matching_session_returns_the_member(self, db_session):
        member = _member(db_session, OLD_ADDRESS)
        own_session = AuthSession(member_id=member.id, jti="own")

        user = deps._get_verified_user(db_session, member.email, own_session)

        assert user.id == member.id
