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
from app.services.p4x_service import get_account_balance


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
        email="admin@test.at",
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
            member_id=member.id,
            role_id="phil-xxxx",
            startdate=date(2020, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    return member


def _login(db, _client, member: Member) -> dict:
    token, _, _ = create_user_session(db, member)
    return {"Authorization": f"Bearer {token}"}


def _create_account(db) -> P4xAccount:
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
    return account


class TestAccountCRUD:
    def test_create_account(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, client, admin)

        resp = client.post(
            "/api/p4x/admin/accounts",
            json={
                "iban": "AT94 2011 1000 0530 1947",
                "bic": "GIBAATWWXXX",
                "label": "Testkonto",
                "init_date": "2020-01-01",
                "init_balance": 500.0,
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["iban"] == "AT94 2011 1000 0530 1947"
        assert data["balance"] == 500.0

    def test_create_duplicate_iban_rejected(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, client, admin)
        _create_account(db_session)

        resp = client.post(
            "/api/p4x/admin/accounts",
            json={
                "iban": "AT942011100005301947",
                "bic": "GIBAATWWXXX",
                "label": "Dup",
                "init_date": "2020-01-01",
                "init_balance": 0.0,
            },
            headers=headers,
        )
        assert resp.status_code == 409

    def test_update_account(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, client, admin)
        account = _create_account(db_session)

        resp = client.put(
            f"/api/p4x/admin/accounts/{account.id}",
            json={
                "iban": "AT942011100005301947",
                "bic": "GIBAATWWXXX",
                "label": "Neuer Name",
                "init_date": "2017-01-01",
                "init_balance": 200.0,
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["label"] == "Neuer Name"

    def test_delete_account_no_transactions(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, client, admin)
        account = _create_account(db_session)

        resp = client.delete(
            f"/api/p4x/admin/accounts/{account.id}",
            headers=headers,
        )
        assert resp.status_code == 200

    def test_delete_account_with_transactions_rejected(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, client, admin)
        account = _create_account(db_session)

        db_session.add(
            P4xTransaction(
                sha256_hash="test",
                booking=date(2026, 1, 1),
                valuation=date(2026, 1, 1),
                iban="AT00",
                amount=10.0,
                subject="test",
                p4x_account_id=account.id,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        db_session.commit()

        resp = client.delete(
            f"/api/p4x/admin/accounts/{account.id}",
            headers=headers,
        )
        assert resp.status_code == 409

    def test_requires_p4x_admin(self, db_session, client):
        _seed(db_session)
        resp = client.post(
            "/api/p4x/admin/accounts",
            json={
                "iban": "AT00TEST",
                "bic": "TEST",
                "label": "X",
                "init_date": "2020-01-01",
                "init_balance": 0.0,
            },
        )
        assert resp.status_code == 401


class TestAccountBalance:
    def test_balance_with_transactions(self, db_session):
        account = _create_account(db_session)
        for i in range(3):
            db_session.add(
                P4xTransaction(
                    sha256_hash=f"bal_{i}",
                    booking=date(2026, 1, 10 + i),
                    valuation=date(2026, 1, 10 + i),
                    iban="AT00",
                    amount=50.0,
                    subject="test",
                    p4x_account_id=account.id,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
        db_session.commit()

        balance = get_account_balance(db_session, account)
        assert balance == 250.0  # 100 init + 3*50

    def test_balance_up_to_date(self, db_session):
        account = _create_account(db_session)
        db_session.add(
            P4xTransaction(
                sha256_hash="before",
                booking=date(2026, 1, 1),
                valuation=date(2026, 1, 1),
                iban="AT00",
                amount=50.0,
                subject="test",
                p4x_account_id=account.id,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        db_session.add(
            P4xTransaction(
                sha256_hash="after",
                booking=date(2026, 6, 1),
                valuation=date(2026, 6, 1),
                iban="AT00",
                amount=200.0,
                subject="test",
                p4x_account_id=account.id,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        db_session.commit()

        balance_jan = get_account_balance(db_session, account, date(2026, 1, 31))
        assert balance_jan == 150.0  # 100 + 50

        balance_jun = get_account_balance(db_session, account, date(2026, 6, 30))
        assert balance_jun == 350.0  # 100 + 50 + 200


class TestDashboard:
    def test_dashboard_requires_auth(self, db_session, client):
        resp = client.get("/api/p4x/accounts")
        assert resp.status_code == 401

    def test_dashboard_returns_accounts(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, client, admin)
        _create_account(db_session)

        resp = client.get("/api/p4x/accounts", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["accounts"]) == 1
        assert data["accounts"][0]["label"] == "Girokonto"
        assert "warnings_partner" in data
        assert "warnings_category" in data

    def test_dashboard_warnings_have_count_and_preview(self, db_session, client):
        _seed(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, client, admin)

        resp = client.get("/api/p4x/accounts", headers=headers)
        data = resp.json()
        assert "count" in data["warnings_partner"]
        assert "preview" in data["warnings_partner"]
