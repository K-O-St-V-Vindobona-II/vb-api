"""A member row created outside the ORM is neither open nor invisible by omission."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.member import Member
from app.models.org import Org
from app.models.state import State
from app.services import standesdb_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_FLAG_COLUMNS = ("gruender", "entlassen", "verstorben", "chroniclemail", "auth_locked")


@pytest.fixture
def org_and_state(db_session: Session) -> None:
    db_session.add_all(
        [Org(id="vbw", label="V", order=1), State(id="up", label="U", order=1)]
    )
    db_session.commit()


def _sql_insert(db_session: Session, **columns: object) -> None:
    names = ", ".join(columns)
    values = ", ".join(f":{name}" for name in columns)
    db_session.execute(
        text(f"INSERT INTO members ({names}) VALUES ({values})"),  # noqa: S608
        columns,
    )
    db_session.commit()


class TestFlagsOfARowInsertedOutsideTheOrm:
    def test_flags_have_safe_defaults(
        self, db_session: Session, org_and_state: None
    ) -> None:
        _sql_insert(
            db_session, vorname="Roh", nachname="Zeile", org_id="vbw", state_id="up"
        )

        row = db_session.execute(
            text(
                "SELECT entlassen, verstorben, gruender, chroniclemail, auth_locked "
                "FROM members"
            )
        ).one()

        assert tuple(row) == (False, False, False, False, True)

    def test_such_a_row_is_locked_when_read_through_the_orm(
        self, db_session: Session, org_and_state: None
    ) -> None:
        _sql_insert(
            db_session,
            vorname="Roh",
            nachname="Zeile",
            org_id="vbw",
            state_id="up",
            auth_password="hash",
        )

        member = db_session.query(Member).one()

        assert member.auth_locked is True

    def test_such_a_row_is_counted_as_present(
        self, db_session: Session, org_and_state: None
    ) -> None:
        _sql_insert(
            db_session, vorname="Roh", nachname="Zeile", org_id="vbw", state_id="up"
        )

        assert standesdb_service.get_member_stats(db_session)["present"]["vbw"] == 1

    def test_an_explicit_unlocked_row_stays_unlocked(
        self, db_session: Session, org_and_state: None
    ) -> None:
        _sql_insert(
            db_session,
            vorname="Offen",
            nachname="Zeile",
            org_id="vbw",
            state_id="up",
            auth_locked=False,
        )

        assert db_session.query(Member).one().auth_locked is False

    @pytest.mark.parametrize("column", _FLAG_COLUMNS)
    def test_a_flag_cannot_be_set_to_null(
        self, db_session: Session, org_and_state: None, column: str
    ) -> None:
        with pytest.raises(IntegrityError):
            _sql_insert(
                db_session,
                vorname="A",
                nachname="B",
                org_id="vbw",
                state_id="up",
                **{column: None},
            )
