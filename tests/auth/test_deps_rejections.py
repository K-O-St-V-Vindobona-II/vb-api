"""Regression tests for the rejection path of the access-token authentication.

A rejection must be a fresh exception every time: Python appends the traceback
of each raise to the exception object, and with it every frame's locals (the
token, the database session), so a shared instance grows for the life of the
process with every unauthenticated request.
"""

import uuid

import jwt
import pytest

from app.core.security import ALGORITHM, SECRET_KEY, create_access_token
from app.models.auth_session import AuthSession
from app.models.member import Member
from app.services import auth_service
from app.services.auth_service import InvalidTokenError

REPEATS = 25
MESSAGE = "Anmeldedaten ungültig."


def _member_with_session(db, jti: str) -> Member:
    member = Member(email=f"{jti}@vindobona.at", auth_locked=False)
    db.add(member)
    db.commit()
    db.add(AuthSession(member_id=member.id, jti=jti))
    db.commit()
    return member


def _garbled_token(db):
    auth_service.authenticate_access_token(db, "not-a-jwt")


def _token_without_jti(db):
    token = jwt.encode({"sub": "member@vindobona.at"}, SECRET_KEY, algorithm=ALGORITHM)
    auth_service.authenticate_access_token(db, token)


def _unknown_session(db):
    token, _ = create_access_token(subject="member@vindobona.at")
    auth_service.authenticate_access_token(db, token)


def _unknown_member(db):
    jti = str(uuid.uuid4())
    _member_with_session(db, jti)
    token, _ = create_access_token(subject="nobody@vindobona.at", jti_override=jti)
    auth_service.authenticate_access_token(db, token)


REJECTIONS = [_garbled_token, _token_without_jti, _unknown_session, _unknown_member]


def _reject(trigger, db):
    with pytest.raises(InvalidTokenError) as info:
        trigger(db)
    return info.value


def _traceback_depth(error):
    depth = 0
    entry = error.__traceback__
    while entry is not None:
        depth += 1
        entry = entry.tb_next
    return depth


class TestRejectionsAreIndependent:
    def test_module_holds_no_exception_instance(self):
        shared = [
            name
            for name, value in vars(auth_service).items()
            if isinstance(value, BaseException)
        ]

        assert shared == []

    @pytest.mark.parametrize("trigger", REJECTIONS, ids=lambda t: t.__name__)
    def test_every_rejection_is_a_new_error_with_the_same_message(
        self, db_session, trigger
    ):
        first = _reject(trigger, db_session)
        second = _reject(trigger, db_session)

        assert first is not second
        assert str(first) == MESSAGE

    @pytest.mark.parametrize("trigger", REJECTIONS, ids=lambda t: t.__name__)
    def test_traceback_does_not_grow_with_repeated_rejections(
        self, db_session, trigger
    ):
        depth_after_first = _traceback_depth(_reject(trigger, db_session))

        for _ in range(REPEATS):
            latest = _reject(trigger, db_session)

        assert _traceback_depth(latest) == depth_after_first

    def test_a_protected_route_answers_401_with_a_bearer_challenge(self, client):
        response = client.get(
            "/api/members/me", headers={"Authorization": "Bearer not-a-jwt"}
        )

        assert response.status_code == 401
        assert response.json() == {"detail": MESSAGE}
        assert response.headers["WWW-Authenticate"] == "Bearer"
