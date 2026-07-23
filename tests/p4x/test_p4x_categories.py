from datetime import UTC, date, datetime

import bcrypt

from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_transaction import P4xTransaction
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import create_user_session
from app.services.p4x_service import get_category_usage


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


def _login_admin(db, _client) -> dict:
    pw = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
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


def _create_category(db, name: str = "eingang.spende") -> P4xCategory:
    cat = P4xCategory(
        name=name,
        label="Spende",
        background_color="#336600",
        text_color="#ffffff",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


class TestCategoryCRUD:
    def test_list_categories(self, db_session, client):
        _seed(db_session)
        headers = _login_admin(db_session, client)
        _create_category(db_session)

        resp = client.get("/api/p4x/admin/categories", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "eingang.spende"
        assert "used" in data[0]

    def test_create_category(self, db_session, client):
        _seed(db_session)
        headers = _login_admin(db_session, client)

        resp = client.post(
            "/api/p4x/admin/categories",
            json={
                "name": "test.cat",
                "label": "Test",
                "background_color": "#ff0000",
                "text_color": "#ffffff",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "test.cat"

    def test_create_duplicate_name_rejected(self, db_session, client):
        _seed(db_session)
        headers = _login_admin(db_session, client)
        _create_category(db_session)

        resp = client.post(
            "/api/p4x/admin/categories",
            json={
                "name": "eingang.spende",
                "label": "Dup",
                "background_color": "#000",
                "text_color": "#fff",
            },
            headers=headers,
        )
        assert resp.status_code == 409

    def test_update_category(self, db_session, client):
        _seed(db_session)
        headers = _login_admin(db_session, client)
        cat = _create_category(db_session)

        resp = client.put(
            f"/api/p4x/admin/categories/{cat.id}",
            json={
                "name": "eingang.spende",
                "label": "Neuer Name",
                "background_color": "#336600",
                "text_color": "#ffffff",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["label"] == "Neuer Name"

    def test_delete_category(self, db_session, client):
        _seed(db_session)
        headers = _login_admin(db_session, client)
        cat = _create_category(db_session)

        resp = client.delete(
            f"/api/p4x/admin/categories/{cat.id}",
            headers=headers,
        )
        assert resp.status_code == 200

    def test_delete_protected_category_rejected(self, db_session, client):
        _seed(db_session)
        headers = _login_admin(db_session, client)
        cat = P4xCategory(
            name="protected.cat",
            label="Protected",
            background_color="#000",
            text_color="#fff",
            protected=True,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cat)
        db_session.commit()

        resp = client.delete(
            f"/api/p4x/admin/categories/{cat.id}",
            headers=headers,
        )
        assert resp.status_code == 409

    def test_delete_in_use_category_rejected(self, db_session, client):
        _seed(db_session)
        headers = _login_admin(db_session, client)
        cat = _create_category(db_session)

        account = P4xAccount(
            iban="AT00TEST",
            bic="TEST",
            label="Test",
            init_date=date(2020, 1, 1),
            init_balance=0,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(account)
        db_session.commit()

        db_session.add(
            P4xCategoryFilter(
                name="uses_cat",
                p4x_account_id=account.id,
                subject_mode="equals",
                p4x_category_id=cat.id,
                subject="test",
                created_at=_now(),
                updated_at=_now(),
            )
        )
        db_session.commit()

        resp = client.delete(
            f"/api/p4x/admin/categories/{cat.id}",
            headers=headers,
        )
        assert resp.status_code == 409

    def test_invalid_hex_color_rejected(self, db_session, client):
        _seed(db_session)
        headers = _login_admin(db_session, client)

        resp = client.post(
            "/api/p4x/admin/categories",
            json={
                "name": "bad.color",
                "label": "Bad",
                "background_color": "not-hex",
                "text_color": "#fff",
            },
            headers=headers,
        )
        assert resp.status_code == 422


class TestCategoryUsage:
    def test_usage_counts(self, db_session):
        cat = _create_category(db_session)
        account = P4xAccount(
            iban="AT00TEST",
            bic="TEST",
            label="Test",
            init_date=date(2020, 1, 1),
            init_balance=0,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(account)
        db_session.commit()

        db_session.add(
            P4xCategoryFilter(
                name="f1",
                p4x_account_id=account.id,
                subject_mode="equals",
                p4x_category_id=cat.id,
                subject="test",
                created_at=_now(),
                updated_at=_now(),
            )
        )
        db_session.commit()

        tx = P4xTransaction(
            sha256_hash="usage_test",
            booking=date(2026, 1, 1),
            valuation=date(2026, 1, 1),
            iban="AT00",
            amount=10.0,
            subject="test",
            p4x_account_id=account.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(tx)
        db_session.commit()
        db_session.add(
            P4xCategoryDirect(
                p4x_transaction_id=tx.id,
                p4x_category_id=cat.id,
                amount=10.0,
            )
        )
        db_session.commit()

        usage = get_category_usage(db_session, cat)
        assert usage["filter"] == 1
        assert usage["direct"] == 1
