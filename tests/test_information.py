"""Tests für den Information/Payment Endpoint."""

import bcrypt
from sqlalchemy import text

from app.models.member import Member
from app.models.org import Org
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


def _create_p4x_tables(db):
    """Create the raw p4x_fees and p4x_accounts
    tables (not managed by ORM) and clear any
    leftover data from prior tests."""
    db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS p4x_fees ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  start TEXT NOT NULL,"
            "  fee REAL NOT NULL,"
            "  protected INTEGER DEFAULT 0"
            ")"
        )
    )
    db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS p4x_accounts ("
            "  id INTEGER PRIMARY KEY,"
            "  iban TEXT NOT NULL,"
            "  bic TEXT NOT NULL,"
            "  label TEXT,"
            "  init_date TEXT,"
            "  init_balance REAL DEFAULT 0"
            ")"
        )
    )
    db.execute(text("DELETE FROM p4x_fees"))
    db.execute(text("DELETE FROM p4x_accounts"))
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
        _create_p4x_tables(db_session)
        db_session.execute(
            text(
                "INSERT INTO p4x_accounts"
                " (id, iban, bic, label,"
                "  init_date, init_balance)"
                " VALUES"
                " (1, 'AT941234567890123456',"
                "  'GIBAATWWXXX', 'AH-Kassa',"
                "  '2020-01-01', 0)"
            )
        )
        db_session.execute(
            text(
                "INSERT INTO p4x_fees"
                " (start, fee, protected)"
                " VALUES ('2020-01-01', 10, 0)"
            )
        )
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
        _create_p4x_tables(db_session)
        db_session.execute(
            text(
                "INSERT INTO p4x_accounts"
                " (id, iban, bic, label,"
                "  init_date, init_balance)"
                " VALUES"
                " (1, 'AT941234567890123456',"
                "  'GIBAATWWXXX', 'AH-Kassa',"
                "  '2020-01-01', 0)"
            )
        )
        db_session.execute(
            text(
                "INSERT INTO p4x_fees"
                " (start, fee, protected)"
                " VALUES ('2020-01-01', 25, 0)"
            )
        )
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
