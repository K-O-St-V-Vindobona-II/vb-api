"""Regression coverage for the float -> Numeric money migration (Alembic
revision 223350b69edd). Targets the three highest-risk functions identified
during the migration audit — none had any existing precision-drift test —
with amounts chosen to expose classic binary-float rounding error (repeated
0.10-style additions that don't sum exactly under float arithmetic but must
under Decimal)."""

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.member import Member
from app.models.org import Org
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_fee import P4xFee
from app.models.p4x_transaction import P4xTransaction
from app.models.state import State
from app.services.p4x_service import (
    calculate_fee_balance,
    get_account_balance,
    set_category_direct,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _create_account(db, init_balance: Decimal = Decimal(0)) -> P4xAccount:
    account = P4xAccount(
        iban="AT942011100005301947",
        bic="GIBAATWWXXX",
        label="Girokonto",
        init_date=date(2015, 1, 1),
        init_balance=init_balance,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


class TestGetAccountBalancePrecision:
    """0.10 repeated 10 times is the textbook float-drift example: under
    binary float arithmetic, sum([0.10] * 10) != 1.00 exactly. Under
    Decimal(12,2), it must."""

    def test_many_small_amounts_sum_exactly(self, db_session):
        account = _create_account(db_session, init_balance=Decimal("10.00"))
        for i in range(10):
            db_session.add(
                P4xTransaction(
                    sha256_hash=f"drift_{i}",
                    booking=date(2020, 1, 1),
                    valuation=date(2020, 1, 1),
                    iban="AT001",
                    amount=Decimal("0.10"),
                    subject="Test",
                    p4x_account_id=account.id,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
        db_session.commit()

        balance = get_account_balance(db_session, account)
        assert balance == Decimal("11.00")
        assert isinstance(balance, Decimal)


class TestSetCategoryDirectPrecision:
    """The split-sum validation used to tolerate a 2-decimal rounding error
    to work around float drift (round(float(amount) - total, 2) != 0.0).
    With Decimal, splits that sum exactly must be accepted with NO
    tolerance, and the rounding workaround is no longer needed."""

    def _seed_transaction_and_category(self, db, amount: Decimal):
        account = _create_account(db)
        category = P4xCategory(
            name="test.split",
            label="Split",
            background_color="#000",
            text_color="#fff",
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(category)
        db.commit()
        db.refresh(category)

        tx = P4xTransaction(
            sha256_hash="split_tx",
            booking=date(2020, 1, 1),
            valuation=date(2020, 1, 1),
            iban="AT001",
            amount=amount,
            subject="Split test",
            p4x_account_id=account.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        return tx, category

    def test_many_small_splits_summing_exactly_are_accepted(self, db_session):
        """10 splits of 0.10 each -> exactly 1.00, no float drift possible
        with Decimal arithmetic, so the strict (non-rounded) comparison
        must still accept this."""
        tx, category = self._seed_transaction_and_category(
            db_session, amount=Decimal("1.00")
        )
        slots = [{"p4x_category_id": category.id, "amount": "0.10"} for _ in range(10)]

        error = set_category_direct(db_session, tx, slots)

        assert error is None
        directs = (
            db_session.query(P4xCategoryDirect)
            .filter(P4xCategoryDirect.p4x_transaction_id == tx.id)
            .all()
        )
        assert len(directs) == 10
        assert sum(d.amount for d in directs) == Decimal("1.00")

    def test_splits_off_by_one_cent_are_rejected(self, db_session):
        tx, category = self._seed_transaction_and_category(
            db_session, amount=Decimal("100.00")
        )
        slots = [
            {"p4x_category_id": category.id, "amount": "40.00"},
            {"p4x_category_id": category.id, "amount": "59.99"},
        ]

        error = set_category_direct(db_session, tx, slots)

        assert error == (
            "Summe der Beträge stimmt nicht mit dem Transaktionsbetrag überein."
        )


class TestCalculateFeeBalancePrecision:
    """Multi-month iterative balance calculation with cent-level fee/payment
    amounts — the audit flagged this as drift-prone under float
    (start_balance -= fee_for_month(...) accumulated over many months)."""

    def test_balance_after_several_months_is_exact(self, db_session):
        db_session.add_all(
            [
                Org(id="vbw", label="VBW", order=1),
                State(id="up", label="UP", order=1),
            ]
        )
        db_session.commit()

        # FEE_CATEGORY_ID is hardcoded to 1 in p4x_service.py.
        category = P4xCategory(
            id=1,
            name="eingang.mitgliedsbeitrag",
            label="Mitgliedsbeitrag",
            background_color="#336600",
            text_color="#ffffff",
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(category)
        db_session.add(
            P4xFee(start=date(2017, 1, 1), fee=Decimal("10.10"), protected=True)
        )
        db_session.commit()

        member = Member(
            vorname="Test",
            nachname="User",
            couleurname="Precisionius",
            email="precision@test.at",
            auth_password="x",
            auth_locked=False,
            org_id="vbw",
            state_id="up",
            p4x_init_date=date(2017, 1, 1),
            p4x_init_balance=Decimal("0.00"),
            p4x_freed=False,
        )
        db_session.add(member)
        db_session.commit()
        db_session.refresh(member)

        balance = calculate_fee_balance(
            db_session, member, start_date_str="2017-01-01", end_date_str="2017-03-31"
        )

        assert balance is not None
        assert isinstance(balance["end_balance"], Decimal)
        # 3 months (Jan-Mar) of 10.10 fee, no payments -> exactly -30.30.
        assert balance["end_balance"] == Decimal("-30.30")
        assert balance["sum"]["fees"] == Decimal("-30.30")
