from datetime import UTC, date, datetime

from app.models.p4x_fee import P4xFee
from app.services.p4x_service import (
    create_fee,
    delete_fee,
    fee_for_month,
    get_all_fees,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _seed_fees(db) -> None:
    db.add_all(
        [
            P4xFee(start=date(2017, 1, 1), fee=10.0, protected=True),
            P4xFee(start=date(2024, 6, 1), fee=15.0, protected=False),
        ]
    )
    db.commit()


class TestFeeForMonth:
    def test_before_any_fee(self, db_session):
        _seed_fees(db_session)
        assert fee_for_month(db_session, date(2016, 6, 15)) == 0.0

    def test_first_period(self, db_session):
        _seed_fees(db_session)
        assert fee_for_month(db_session, date(2017, 1, 10)) == 10.0
        assert fee_for_month(db_session, date(2020, 3, 10)) == 10.0
        assert fee_for_month(db_session, date(2024, 5, 10)) == 10.0

    def test_second_period(self, db_session):
        _seed_fees(db_session)
        assert fee_for_month(db_session, date(2024, 6, 10)) == 15.0
        assert fee_for_month(db_session, date(2026, 1, 10)) == 15.0

    def test_exact_start_date(self, db_session):
        _seed_fees(db_session)
        assert fee_for_month(db_session, date(2024, 6, 1)) == 15.0

    def test_no_fees_configured(self, db_session):
        assert fee_for_month(db_session, date(2026, 1, 1)) == 0.0


class TestFeeConfigCRUD:
    def test_get_all_fees(self, db_session):
        _seed_fees(db_session)
        fees = get_all_fees(db_session)
        assert len(fees) == 2
        assert fees[0].fee == 10.0
        assert fees[1].fee == 15.0

    def test_create_fee(self, db_session):
        fee, error = create_fee(db_session, 2027, 1, 20.0)
        assert error is None
        assert fee is not None
        assert fee.fee == 20.0

    def test_create_fee_in_past_rejected(self, db_session):
        _, error = create_fee(db_session, 2020, 1, 10.0)
        assert error is not None
        assert "Zukunft" in error

    def test_create_fee_duplicate_month_rejected(self, db_session):
        fee, _ = create_fee(db_session, 2027, 6, 20.0)
        assert fee is not None
        _, error = create_fee(db_session, 2027, 6, 25.0)
        assert error is not None
        assert "eindeutig" in error

    def test_delete_unprotected_fee(self, db_session):
        _seed_fees(db_session)
        error = delete_fee(db_session, "2024-06-01")
        assert error is None
        assert len(get_all_fees(db_session)) == 1

    def test_delete_protected_fee_rejected(self, db_session):
        _seed_fees(db_session)
        error = delete_fee(db_session, "2017-01-01")
        assert error is not None
        assert "geschützt" in error.lower() or "nicht gefunden" in error.lower()
        assert len(get_all_fees(db_session)) == 2
