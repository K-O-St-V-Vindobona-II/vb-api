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


class TestEmailIgnoresCase:
    def test_two_addresses_differing_in_case_are_refused(
        self, db_session: Session, org_and_state: None
    ) -> None:
        _sql_insert(
            db_session, vorname="A", nachname="A", org_id="vbw", email="Victim@x.at"
        )

        with pytest.raises(IntegrityError):
            _sql_insert(
                db_session,
                vorname="B",
                nachname="B",
                org_id="vbw",
                email="victim@x.at",
            )

    def test_the_same_address_twice_is_still_refused(
        self, db_session: Session, org_and_state: None
    ) -> None:
        _sql_insert(db_session, vorname="A", nachname="A", org_id="vbw", email="a@x.at")

        with pytest.raises(IntegrityError):
            _sql_insert(
                db_session, vorname="B", nachname="B", org_id="vbw", email="a@x.at"
            )

    def test_members_without_an_address_are_still_fine(
        self, db_session: Session, org_and_state: None
    ) -> None:
        _sql_insert(db_session, vorname="A", nachname="A", org_id="vbw")
        _sql_insert(db_session, vorname="B", nachname="B", org_id="vbw")

        assert db_session.query(Member).count() == 2

    def test_an_exact_lookup_still_uses_a_plain_index(
        self, db_session: Session
    ) -> None:
        definition = db_session.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_members_email'")
        ).scalar_one()

        assert "UNIQUE" not in definition


class TestParentCycles:
    @staticmethod
    def _chain(db_session: Session) -> list[Member]:
        members = [
            Member(vorname=f"C{i}", nachname="X", org_id="vbw", state_id="up")
            for i in range(3)
        ]
        db_session.add_all(members)
        db_session.commit()
        members[1].parent_id = members[0].id
        members[2].parent_id = members[1].id
        db_session.commit()
        return members

    def test_a_member_cannot_be_its_own_parent(
        self, db_session: Session, org_and_state: None
    ) -> None:
        member = Member(vorname="Self", nachname="X", org_id="vbw", state_id="up")
        db_session.add(member)
        db_session.commit()

        member.parent_id = member.id
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_a_grandchild_cannot_become_the_parent_of_the_top(
        self, db_session: Session, org_and_state: None
    ) -> None:
        top, _, bottom = self._chain(db_session)

        top.parent_id = bottom.id
        with pytest.raises(IntegrityError) as exc:
            db_session.commit()

        assert getattr(exc.value.orig, "pgcode", None) == "23514"

    def test_a_direct_child_cannot_become_the_parent_of_its_parent(
        self, db_session: Session, org_and_state: None
    ) -> None:
        top, middle, _ = self._chain(db_session)

        top.parent_id = middle.id
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_a_valid_change_of_parent_works(
        self, db_session: Session, org_and_state: None
    ) -> None:
        top, _, bottom = self._chain(db_session)

        bottom.parent_id = top.id
        db_session.commit()

        assert bottom.parent_id == top.id

    def test_a_parent_can_be_removed(
        self, db_session: Session, org_and_state: None
    ) -> None:
        _, middle, _ = self._chain(db_session)

        middle.parent_id = None
        db_session.commit()

        assert middle.parent_id is None

    def test_a_new_member_can_start_below_an_existing_one(
        self, db_session: Session, org_and_state: None
    ) -> None:
        top, _, _ = self._chain(db_session)

        child = Member(
            vorname="New", nachname="X", org_id="vbw", state_id="up", parent_id=top.id
        )
        db_session.add(child)
        db_session.commit()

        assert child.parent_id == top.id

    def test_the_constraint_alone_refuses_a_self_reference(
        self, db_session: Session, org_and_state: None
    ) -> None:
        member = Member(vorname="Self", nachname="X", org_id="vbw", state_id="up")
        db_session.add(member)
        db_session.commit()
        db_session.execute(
            text("ALTER TABLE members DISABLE TRIGGER members_parent_cycle")
        )

        member.parent_id = member.id
        with pytest.raises(IntegrityError, match="members_parent_not_self_check"):
            db_session.commit()

    def test_a_new_member_below_a_stored_cycle_is_refused_as_well(
        self, db_session: Session, org_and_state: None
    ) -> None:
        first, second = (
            Member(vorname=name, nachname="X", org_id="vbw", state_id="up")
            for name in ("A", "B")
        )
        db_session.add_all([first, second])
        db_session.commit()
        db_session.execute(
            text("ALTER TABLE members DISABLE TRIGGER members_parent_cycle")
        )
        first.parent_id = second.id
        second.parent_id = first.id
        db_session.commit()
        db_session.execute(
            text("ALTER TABLE members ENABLE TRIGGER members_parent_cycle")
        )

        db_session.add(
            Member(
                vorname="New",
                nachname="X",
                org_id="vbw",
                state_id="up",
                parent_id=first.id,
            )
        )
        with pytest.raises(IntegrityError, match="too deep or cyclic"):
            db_session.commit()

    def test_a_stored_cycle_does_not_make_the_check_run_forever(
        self, db_session: Session, org_and_state: None
    ) -> None:
        first, second, outsider = (
            Member(vorname=name, nachname="X", org_id="vbw", state_id="up")
            for name in ("A", "B", "C")
        )
        db_session.add_all([first, second, outsider])
        db_session.commit()
        # A cycle can only exist if the trigger was off when it was written.
        db_session.execute(
            text("ALTER TABLE members DISABLE TRIGGER members_parent_cycle")
        )
        first.parent_id = second.id
        second.parent_id = first.id
        db_session.commit()
        db_session.execute(
            text("ALTER TABLE members ENABLE TRIGGER members_parent_cycle")
        )

        outsider.parent_id = first.id
        with pytest.raises(IntegrityError, match="too deep or cyclic"):
            db_session.commit()
