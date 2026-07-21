"""Tests für den Information/Payment Endpoint."""

import datetime

import bcrypt

from app.models.member import Member
from app.models.org import Org
from app.models.p4x_account import P4xAccount
from app.models.p4x_fee import P4xFee
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import (
    create_user_session,
)


def _seed(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            State(id="fu", label="Fux", order=1),
        ]
    )
    db.commit()
    db.add(
        Role(
            id="x",
            group="chc",
            label="Senior",
            order=1,
        )
    )
    db.commit()


def _login_user(db, _client):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email="user@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Normal",
        nachname="User",
        org_id="vbw",
        state_id="fu",
    )
    db.add(m)
    db.commit()
    token, _, _ = create_user_session(db, m)
    return {"Authorization": f"Bearer {token}"}, m


def _seed_p4x_account(db):
    """The app looks up the single p4x_accounts row by id=1
    (app/api/router_includes/information.py) — a deliberate
    single-account business assumption, not a test artifact."""
    db.add(
        P4xAccount(
            id=1,
            iban="AT941234567890123456",
            bic="GIBAATWWXXX",
            label="AH-Kassa",
            init_date=datetime.date(2020, 1, 1),
            init_balance=0,
        )
    )
    db.commit()


class TestPaymentEndpoint:
    def test_payment_requires_auth(
        self,
        client,
        db_session,
    ):
        """GET /api/information/payment without
        token returns 401."""
        resp = client.get("/api/information/payment")
        assert resp.status_code == 401

    def test_payment_returns_two_entries(
        self,
        client,
        db_session,
    ):
        """Authenticated request returns
        a list with 2 entries."""
        _seed(db_session)
        _seed_p4x_account(db_session)
        db_session.add(P4xFee(start=datetime.date(2020, 1, 1), fee=10, protected=False))
        db_session.commit()
        headers, _ = _login_user(db_session, client)
        resp = client.get(
            "/api/information/payment",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["title"] == "Aktivitas"
        assert data[1]["title"] == "Altherrenschaft"

    def test_payment_dynamic_fee(
        self,
        client,
        db_session,
    ):
        """The AH fee is dynamically calculated
        from p4x_fees table."""
        _seed(db_session)
        _seed_p4x_account(db_session)
        db_session.add(P4xFee(start=datetime.date(2020, 1, 1), fee=25, protected=False))
        db_session.commit()
        headers, _ = _login_user(db_session, client)
        resp = client.get(
            "/api/information/payment",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        ah_entry = data[1]
        assert "25" in ah_entry["fee"]
        assert "EUR" in ah_entry["fee"]
