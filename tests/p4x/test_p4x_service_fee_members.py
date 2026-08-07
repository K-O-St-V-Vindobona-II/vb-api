from datetime import UTC, date, datetime

import bcrypt

from app.models.enums import FeeInterval
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import create_user_session
from app.services.p4x_fee_balance_service import (
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


def _login_admin(db, _client) -> dict:
    """Same pattern as test_p4x_categories.py::_login_admin — a "philchc"
    role grants p4xAdmin."""
    pw = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    db.add(Role(id="phil-xxxx", group="philchc", label="Phil-x", order=1))
    db.commit()
    m = Member(
        email="admin@vbw.at",
        auth_password=pw,
        auth_locked=False,
        vorname="Admin",
        nachname="User",
        org_id="vbw",
        state_id="up",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    db.add(
        MemberRole(
            member_id=m.id,
            role_id="phil-xxxx",
            startdate=date(2020, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    token, _, _ = create_user_session(db, m)
    return {"Authorization": f"Bearer {token}"}


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
                "p4x_fee_interval": FeeInterval.QUARTERLY,
            },
        )

        db_session.refresh(member)
        assert member.p4x_init_date == date(2020, 6, 15)
        assert member.p4x_init_balance == 50
        assert member.p4x_freed is True
        assert member.p4x_comment == "Sondergenehmigung"
        assert member.p4x_fee_interval == FeeInterval.QUARTERLY

    def test_update_accepts_raw_string_interval(self, db_session):
        """update_fee_member() is also called directly (not just via the
        HTTP endpoint, see TestUpdateFeeMemberViaHttpEndpoint) — the raw-str
        branch must work standalone, same as p4x_init_date's str branch."""
        _seed_base(db_session)
        member = _create_member(db_session)

        update_fee_member(
            db_session,
            member,
            {
                "p4x_init_date": "2020-06-15",
                "p4x_init_balance": 0,
                "p4x_freed": False,
                "p4x_fee_interval": "annual",
            },
        )

        db_session.refresh(member)
        assert member.p4x_fee_interval == FeeInterval.ANNUAL

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


class TestUpdateFeeMemberViaHttpEndpoint:
    """Exercises the REAL HTTP endpoint (not update_fee_member() called
    directly), specifically for p4x_fee_interval — the tests in
    TestUpdateFeeMember above call the service directly with a hand-built
    dict, which does NOT catch the exact bug class that already bit
    p4x_init_date once in production: FeeMemberUpdateRequest.model_dump()
    hands the service a real FeeInterval enum instance parsed from a raw
    JSON string, not the string itself — a Pydantic strict=True field
    without Field(strict=False) would reject that raw string outright, and
    a service-side isinstance() check written against the wrong type would
    silently no-op instead of erroring. Only a real request through the
    router exercises that whole chain."""

    def test_p4x_fee_interval_round_trips_through_real_endpoint(
        self, db_session, client
    ):
        _seed_base(db_session)
        headers = _login_admin(db_session, client)
        member = _create_member(db_session, email="target@t.at")

        resp = client.post(
            f"/api/p4x/admin/fee-members/{member.id}",
            headers=headers,
            json={
                "p4x_init_date": "2020-06-15",
                "p4x_init_balance": 50,
                "p4x_freed": False,
                "p4x_fee_interval": "quarterly",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["p4x_fee_interval"] == "quarterly"

        db_session.refresh(member)
        assert member.p4x_fee_interval == FeeInterval.QUARTERLY
        # Same class of bug as the historical p4x_init_date incident: assert
        # the OTHER fields also actually persisted, not just interval.
        assert member.p4x_init_date == date(2020, 6, 15)
        assert member.p4x_init_balance == 50

    def test_p4x_fee_interval_defaults_to_monthly_when_omitted(
        self, db_session, client
    ):
        _seed_base(db_session)
        headers = _login_admin(db_session, client)
        member = _create_member(db_session, email="target2@t.at")

        resp = client.post(
            f"/api/p4x/admin/fee-members/{member.id}",
            headers=headers,
            json={
                "p4x_init_date": "2020-06-15",
                "p4x_init_balance": 0,
                "p4x_freed": False,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["p4x_fee_interval"] == "monthly"
