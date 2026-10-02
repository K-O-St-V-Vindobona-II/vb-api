"""Regression tests for the rejection path of the authentication dependency.

A rejection must be a fresh exception every time: Python appends the traceback
of each raise to the exception object, and with it every frame's locals (the
token, the database session), so a shared instance grows for the life of the
process with every unauthenticated request.
"""

import jwt
import pytest
from fastapi import HTTPException, status

from app.api import deps
from app.core.security import ALGORITHM, SECRET_KEY

REPEATS = 25


def _garbled_token(db):  # noqa: ARG001 - common trigger signature
    deps._decode_token("not-a-jwt")


def _token_without_jti(db):  # noqa: ARG001 - common trigger signature
    token = jwt.encode({"sub": "member@vindobona.at"}, SECRET_KEY, algorithm=ALGORITHM)
    deps._decode_token(token)


def _unknown_session(db):
    deps._get_session_record(db, "unknown-jti")


def _unknown_member(db):
    deps._get_verified_user(db, "nobody@vindobona.at")


REJECTIONS = [_garbled_token, _token_without_jti, _unknown_session, _unknown_member]


def _reject(trigger, db):
    with pytest.raises(HTTPException) as info:
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
            for name, value in vars(deps).items()
            if isinstance(value, BaseException)
        ]

        assert shared == []

    @pytest.mark.parametrize("trigger", REJECTIONS, ids=lambda t: t.__name__)
    def test_every_rejection_is_a_new_401(self, db_session, trigger):
        first = _reject(trigger, db_session)
        second = _reject(trigger, db_session)

        assert first is not second
        assert first.status_code == status.HTTP_401_UNAUTHORIZED
        assert first.detail == "Anmeldedaten ungültig."
        assert first.headers == {"WWW-Authenticate": "Bearer"}

    @pytest.mark.parametrize("trigger", REJECTIONS, ids=lambda t: t.__name__)
    def test_traceback_does_not_grow_with_repeated_rejections(
        self, db_session, trigger
    ):
        depth_after_first = _traceback_depth(_reject(trigger, db_session))

        for _ in range(REPEATS):
            latest = _reject(trigger, db_session)

        assert _traceback_depth(latest) == depth_after_first
