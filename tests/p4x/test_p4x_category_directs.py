from datetime import UTC, date, datetime

from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
from app.models.p4x_transaction import P4xTransaction
from app.services.p4x_service import (
    apply_all_category_filters,
    set_category_direct,
    unset_category_direct,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _seed(db) -> tuple[P4xAccount, P4xCategory, P4xTransaction]:
    account = P4xAccount(
        iban="AT00TEST",
        bic="GIBAATWWXXX",
        label="Test",
        init_date=date(2020, 1, 1),
        init_balance=0,
        created_at=_now(),
        updated_at=_now(),
    )
    cat = P4xCategory(
        name="test.cat",
        label="Test",
        background_color="#000",
        text_color="#fff",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add_all([account, cat])
    db.commit()
    db.refresh(account)
    db.refresh(cat)

    tx = P4xTransaction(
        sha256_hash="direct_test_tx",
        booking=date(2026, 3, 15),
        valuation=date(2026, 3, 15),
        iban="AT001",
        amount=100.0,
        subject="Test Transaktion",
        p4x_account_id=account.id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return account, cat, tx


class TestSetCategoryDirect:
    def test_single_category(self, db_session):
        _, cat, tx = _seed(db_session)
        error = set_category_direct(
            db_session,
            tx,
            [
                {"p4x_category_id": cat.id, "amount": 100.0},
            ],
        )
        assert error is None

        directs = (
            db_session.query(P4xCategoryDirect)
            .filter(
                P4xCategoryDirect.p4x_transaction_id == tx.id,
                P4xCategoryDirect.deleted_at.is_(None),
            )
            .all()
        )
        assert len(directs) == 1
        assert directs[0].amount == 100.0

    def test_multiple_categories_split(self, db_session):
        _account, cat, tx = _seed(db_session)
        cat2 = P4xCategory(
            name="cat2",
            label="Cat2",
            background_color="#111",
            text_color="#eee",
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cat2)
        db_session.commit()
        db_session.refresh(cat2)

        error = set_category_direct(
            db_session,
            tx,
            [
                {"p4x_category_id": cat.id, "amount": 60.0},
                {"p4x_category_id": cat2.id, "amount": 40.0},
            ],
        )
        assert error is None

        directs = (
            db_session.query(P4xCategoryDirect)
            .filter(
                P4xCategoryDirect.p4x_transaction_id == tx.id,
                P4xCategoryDirect.deleted_at.is_(None),
            )
            .all()
        )
        assert len(directs) == 2
        amounts = sorted(d.amount for d in directs)
        assert amounts == [40.0, 60.0]

    def test_wrong_sum_rejected(self, db_session):
        _, cat, tx = _seed(db_session)
        error = set_category_direct(
            db_session,
            tx,
            [
                {"p4x_category_id": cat.id, "amount": 50.0},
            ],
        )
        assert error is not None
        assert "Summe" in error

    def test_replaces_existing_directs(self, db_session):
        _, cat, tx = _seed(db_session)
        set_category_direct(
            db_session,
            tx,
            [
                {"p4x_category_id": cat.id, "amount": 100.0},
            ],
        )

        cat2 = P4xCategory(
            name="replacement",
            label="R",
            background_color="#222",
            text_color="#ddd",
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cat2)
        db_session.commit()
        db_session.refresh(cat2)

        set_category_direct(
            db_session,
            tx,
            [
                {"p4x_category_id": cat2.id, "amount": 100.0},
            ],
        )

        active = (
            db_session.query(P4xCategoryDirect)
            .filter(
                P4xCategoryDirect.p4x_transaction_id == tx.id,
                P4xCategoryDirect.deleted_at.is_(None),
            )
            .all()
        )
        assert len(active) == 1
        assert active[0].p4x_category_id == cat2.id

    def test_empty_slots_filtered(self, db_session):
        _, cat, tx = _seed(db_session)
        error = set_category_direct(
            db_session,
            tx,
            [
                {"p4x_category_id": cat.id, "amount": 100.0},
                {"p4x_category_id": None, "amount": 0},
                {"p4x_category_id": None, "amount": 0},
            ],
        )
        assert error is None

        directs = (
            db_session.query(P4xCategoryDirect)
            .filter(
                P4xCategoryDirect.p4x_transaction_id == tx.id,
                P4xCategoryDirect.deleted_at.is_(None),
            )
            .all()
        )
        assert len(directs) == 1


class TestUnsetCategoryDirect:
    def test_unset_soft_deletes(self, db_session):
        _, cat, tx = _seed(db_session)
        set_category_direct(
            db_session,
            tx,
            [
                {"p4x_category_id": cat.id, "amount": 100.0},
            ],
        )

        unset_category_direct(db_session, tx)

        active = (
            db_session.query(P4xCategoryDirect)
            .filter(
                P4xCategoryDirect.p4x_transaction_id == tx.id,
                P4xCategoryDirect.deleted_at.is_(None),
            )
            .all()
        )
        assert len(active) == 0

    def test_unset_triggers_filter_reapply(self, db_session):
        account, cat, tx = _seed(db_session)

        f = P4xCategoryFilter(
            name="auto_filter",
            p4x_account_id=account.id,
            subject_mode="equals",
            subject="Test Transaktion",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(f)
        db_session.commit()

        apply_all_category_filters(db_session)
        hits_before = (
            db_session.query(P4xCategoryFilterHit)
            .filter(
                P4xCategoryFilterHit.p4x_transaction_id == tx.id,
            )
            .count()
        )
        assert hits_before == 1

        set_category_direct(
            db_session,
            tx,
            [
                {"p4x_category_id": cat.id, "amount": 100.0},
            ],
        )
        apply_all_category_filters(db_session)
        hits_with_direct = (
            db_session.query(P4xCategoryFilterHit)
            .filter(
                P4xCategoryFilterHit.p4x_transaction_id == tx.id,
            )
            .count()
        )
        assert hits_with_direct == 0

        unset_category_direct(db_session, tx)
        hits_after_unset = (
            db_session.query(P4xCategoryFilterHit)
            .filter(
                P4xCategoryFilterHit.p4x_transaction_id == tx.id,
            )
            .count()
        )
        assert hits_after_unset == 1
