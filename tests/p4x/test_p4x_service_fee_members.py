from datetime import UTC, date, datetime

from app.models.member import Member
from app.models.org import Org
from app.models.state import State
from app.services.p4x_service import (
    get_fee_members,
    search_fee_members,
    update_fee_member,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _seed_base(db) -> None:
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Org(id="vbn", label="VBN", order=2),
            State(id="up", label="UP", order=1),
            State(id="fu", label="FU", order=2),
        ]
    )
    db.commit()


def _create_member(
    db,
    vorname: str = "Max",
    nachname: str = "Mustermann",
    couleurname: str = "Kopernikus",
    email: str = "test@test.at",
    org_id: str = "vbw",
    state_id: str = "up",
    entlassen: bool = False,
    verstorben: bool = False,
) -> Member:
    member = Member(
        vorname=vorname,
        nachname=nachname,
        couleurname=couleurname,
        email=email,
        auth_password="x",
        auth_locked=False,
        org_id=org_id,
        state_id=state_id,
        entlassen=entlassen,
        verstorben=verstorben,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


class TestGetFeeMembers:
    def test_returns_only_fee_eligible_members(self, db_session):
        _seed_base(db_session)
        m1 = _create_member(db_session, email="a@t.at", couleurname="Alpha")
        _create_member(db_session, org_id="vbn", email="b@t.at", couleurname="Beta")
        _create_member(db_session, state_id="fu", email="c@t.at", couleurname="Gamma")

        result = get_fee_members(db_session)
        ids = [m.id for m in result]
        assert m1.id in ids
        assert len(result) == 1


class TestSearchFeeMembers:
    def test_search_returns_matches_by_vorname(self, db_session):
        _seed_base(db_session)
        _create_member(db_session, vorname="Wolfgang", email="w@t.at")

        results = search_fee_members(db_session, "Wolf")
        assert len(results) == 1
        assert results[0]["id"] is not None

    def test_search_returns_matches_by_nachname(self, db_session):
        _seed_base(db_session)
        _create_member(db_session, nachname="Schneider", email="s@t.at")

        results = search_fee_members(db_session, "Schnei")
        assert len(results) == 1

    def test_search_returns_matches_by_couleurname(self, db_session):
        _seed_base(db_session)
        _create_member(db_session, couleurname="Prometheus", email="p@t.at")

        results = search_fee_members(db_session, "Promet")
        assert len(results) == 1

    def test_search_is_case_insensitive(self, db_session):
        """Regression test: Postgres LIKE is case-sensitive, unlike the
        legacy MySQL system's default collation. A search term in a
        different case than the stored name must still match."""
        _seed_base(db_session)
        _create_member(db_session, vorname="Wolfgang", email="w2@t.at")

        results = search_fee_members(db_session, "wolf")
        assert len(results) == 1

    def test_search_short_term_returns_empty(self, db_session):
        _seed_base(db_session)
        _create_member(db_session)

        results = search_fee_members(db_session, "Ma")
        assert results == []

    def test_search_no_match(self, db_session):
        _seed_base(db_session)
        _create_member(db_session)

        results = search_fee_members(db_session, "ZZZZZZZ")
        assert results == []

    def test_search_excludes_non_fee_members(self, db_session):
        _seed_base(db_session)
        _create_member(db_session, vorname="Wolfgang", org_id="vbn", email="w@t.at")

        results = search_fee_members(db_session, "Wolf")
        assert results == []


class TestUpdateFeeMember:
    def test_update_all_fields(self, db_session):
        _seed_base(db_session)
        member = _create_member(db_session)

        update_fee_member(
            db_session,
            member,
            {
                "p4x_init_date": "2020-06-15",
                "p4x_init_balance": 50,
                "p4x_freed": True,
                "p4x_comment": "Sondergenehmigung",
            },
        )

        db_session.refresh(member)
        assert member.p4x_init_date == date(2020, 6, 15)
        assert member.p4x_init_balance == 50
        assert member.p4x_freed is True
        assert member.p4x_comment == "Sondergenehmigung"

    def test_update_with_none_values(self, db_session):
        _seed_base(db_session)
        member = _create_member(db_session)
        member.p4x_init_date = date(2020, 1, 1)
        member.p4x_init_balance = 100
        member.p4x_freed = True
        member.p4x_comment = "old comment"
        db_session.commit()

        update_fee_member(
            db_session,
            member,
            {
                "p4x_init_date": None,
                "p4x_init_balance": None,
                "p4x_freed": None,
                "p4x_comment": None,
            },
        )

        db_session.refresh(member)
        assert member.p4x_init_date is None
        assert member.p4x_init_balance is None
        assert member.p4x_freed is None
        assert member.p4x_comment is None
