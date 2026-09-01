"""HTTP-level tests for the p4x_response_builders fetch-or-404 helpers
(get_transaction_or_404, get_member_or_404) introduced in the Slice 3
router->service extraction. The endpoints wiring them in
(set_transaction_partner, update_transaction, set/unset-category-direct,
get/update_fee_member) previously only had service-level tests that
bypassed the router entirely, leaving these fetch guards untested.
"""

from datetime import UTC, date, datetime

import bcrypt

from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.p4x_account import P4xAccount
from app.models.p4x_transaction import P4xTransaction
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import create_user_session


def _now() -> datetime:
    return datetime.now(UTC)


def _seed(db) -> None:
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            State(id="up", label="UP", order=1),
            Role(id="phil-xxxx", group="philchc", label="Phil-x", order=1),
        ]
    )
    db.commit()


def _create_admin(db) -> Member:
    pw = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
    member = Member(
        vorname="Test",
        nachname="Admin",
        couleurname="Tester",
        email="p4x-fetch-admin@test.at",
        auth_password=pw,
        org_id="vbw",
        state_id="up",
        auth_locked=False,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    db.add(
        MemberRole(
            member_id=member.id_uuid,
            role_id="phil-xxxx",
            startdate=date(2020, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    return member


def _login(db, member: Member) -> dict:
    token, _, _ = create_user_session(db, member)
    return {"Authorization": f"Bearer {token}"}


def _create_transaction(db) -> P4xTransaction:
    account = P4xAccount(
        iban="AT942011100005301947",
        bic="GIBAATWWXXX",
        label="Girokonto",
        init_date=date(2017, 1, 1),
        init_balance=100.0,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    tx = P4xTransaction(
        sha256_hash="fetch_or_404_test_tx",
        booking=date(2026, 3, 15),
        valuation=date(2026, 3, 15),
        iban="AT001",
        amount=42.0,
        subject="Test",
        p4x_account_id=account.id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


class TestGetTransactionOr404:
    def test_set_partner_succeeds_for_existing_transaction(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        tx = _create_transaction(db_session)

        resp = client.post(
            f"/api/p4x/admin/transactions/{tx.id}/set-partner",
            json={"partner": None, "hasDelegatingPartner": False},
            headers=headers,
        )

        assert resp.status_code == 200
        assert resp.json()["id"] == tx.id

    def test_set_partner_404_for_missing_transaction(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)

        resp = client.post(
            "/api/p4x/admin/transactions/99999/set-partner",
            json={"partner": None, "hasDelegatingPartner": False},
            headers=headers,
        )

        assert resp.status_code == 404


class TestGetMemberOr404:
    def test_fee_member_404_for_missing_member(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)

        resp = client.get("/api/p4x/fee-members/99999", headers=headers)

        assert resp.status_code == 404

    def test_update_fee_member_404_for_missing_member(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)

        resp = client.post(
            "/api/p4x/admin/fee-members/99999",
            json={
                "p4x_init_date": "2020-01-01",
                "p4x_init_balance": "0.00",
                "p4x_freed": True,
                "p4x_comment": None,
            },
            headers=headers,
        )

        assert resp.status_code == 404


class TestUpdateFeeMemberEndpoint:
    def test_persists_all_fields_via_http(self, db_session, client):
        """Regression test: FastAPI passes p4x_init_date as a date object
        (not a string) through model_dump() — the service layer must accept
        both, or the date silently gets dropped on every real save.
        """
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)

        target = Member(
            vorname="Debug",
            nachname="Target",
            email="update-fee-member-target@test.at",
            auth_password="x",
            org_id="vbw",
            state_id="up",
            auth_locked=False,
        )
        db_session.add(target)
        db_session.commit()
        db_session.refresh(target)

        resp = client.post(
            f"/api/p4x/admin/fee-members/{target.id}",
            json={
                "p4x_init_date": "2020-06-15",
                "p4x_init_balance": "50.00",
                "p4x_freed": True,
                "p4x_comment": "Sondergenehmigung",
            },
            headers=headers,
        )

        assert resp.status_code == 200
        db_session.refresh(target)
        assert target.p4x_init_date == date(2020, 6, 15)
        assert target.p4x_init_balance == 50
        assert target.p4x_freed is True
        assert target.p4x_comment == "Sondergenehmigung"
