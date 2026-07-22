from datetime import UTC, date, datetime

from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_transaction import P4xTransaction
from app.services.p4x_service import get_sumup_balance


def _now() -> datetime:
    return datetime.now(UTC)


def _seed(db) -> tuple[P4xAccount, P4xCategory]:
    account = P4xAccount(
        id=1,  # get_sumup_balance() looks up SUMUP_ACCOUNT_ID (= 1) explicitly
        iban="AT942011100005301947",
        bic="GIBAATWWXXX",
        label="Girokonto",
        init_date=date(2015, 1, 1),
        init_balance=0,
        created_at=_now(),
        updated_at=_now(),
    )
    cat = P4xCategory(
        name="projekt.bude.sumup",
        label="SumUp",
        background_color="#000",
        text_color="#fff",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add_all([account, cat])
    db.commit()
    db.refresh(account)
    db.refresh(cat)
    return account, cat


def _add_sumup_tx(
    db,
    account: P4xAccount,
    category: P4xCategory,
    amount: float,
    booking: date,
) -> None:
    tx = P4xTransaction(
        sha256_hash=f"sumup_{booking}_{amount}",
        booking=booking,
        valuation=booking,
        iban="",
        amount=amount,
        subject="SumUp",
        p4x_account_id=account.id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(tx)
    db.flush()
    db.add(
        P4xCategoryDirect(
            p4x_transaction_id=tx.id,
            p4x_category_id=category.id,
            amount=amount,
        )
    )
    db.commit()


class TestSumUpBalance:
    def test_empty(self, db_session):
        _seed(db_session)
        result = get_sumup_balance(db_session)
        assert result["in_count"] == 0
        assert result["out_count"] == 0
        assert result["latest"] is None

    def test_with_transactions(self, db_session):
        account, cat = _seed(db_session)
        _add_sumup_tx(db_session, account, cat, 50.0, date(2025, 5, 1))
        _add_sumup_tx(db_session, account, cat, 30.0, date(2025, 5, 10))
        _add_sumup_tx(db_session, account, cat, -20.0, date(2025, 5, 15))

        result = get_sumup_balance(db_session)
        assert result["in_count"] == 2
        assert result["in_sum"] == 80.0
        assert result["out_count"] == 1
        assert result["out_sum"] == -20.0
        assert result["latest"] == "2025-05-15"

    def test_no_account(self, db_session):
        result = get_sumup_balance(db_session)
        assert result["in_count"] == 0

    def test_no_matching_category(self, db_session):
        account = P4xAccount(
            iban="AT00TEST",
            bic="TEST",
            label="Test",
            init_date=date(2015, 1, 1),
            init_balance=0,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(account)
        db_session.commit()
        result = get_sumup_balance(db_session)
        assert result["in_count"] == 0
