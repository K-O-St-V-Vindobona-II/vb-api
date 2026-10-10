"""The single-member balance reads the fee table once, whatever the tracking
period, and applies every fee step to the right month."""

from datetime import date
from decimal import Decimal

import pytest

from app.services.p4x_fee_balance_service import (
    _fee_at,
    _months_from,
    _sum_fees_for_period,
    calculate_fee_balance,
)
from tests.p4x.test_p4x_fee_balance import _create_fee_member, _seed_base


class TestStatementCount:
    @pytest.mark.parametrize(
        ("start", "end"),
        [(None, None), ("2024-07-01", "2024-09-30")],
        ids=["whole period", "later window"],
    )
    def test_statement_count_does_not_grow_with_the_period(
        self, db_session, count_queries, start, end
    ):
        _seed_base(db_session)
        member = _create_fee_member(db_session, p4x_init_date=date(2024, 1, 1))
        counts = []
        for since in (date(2024, 1, 1), date(2017, 1, 1)):
            member.p4x_init_date = since
            db_session.commit()
            db_session.expire_all()
            with count_queries() as counter:
                calculate_fee_balance(db_session, member, start, end)
            counts.append(counter.count)

        assert counts[1] == counts[0]


class TestFeeSteps:
    def test_each_month_takes_the_fee_in_force_on_its_first_day(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session, p4x_init_balance=0, p4x_init_date=date(2024, 4, 1)
        )

        result = calculate_fee_balance(db_session, member, "2024-04-01", "2024-08-31")

        assert result is not None
        fees = [
            (e["booking"], e["amount"])
            for e in result["progress"]
            if e["type"] == "fee"
        ]
        assert fees == [
            ("2024-04-10", Decimal(-10)),
            ("2024-05-10", Decimal(-10)),
            ("2024-06-10", Decimal(-15)),
            ("2024-07-10", Decimal(-15)),
            ("2024-08-10", Decimal(-15)),
        ]
        assert result["end_balance"] == Decimal(-65)

    def test_start_balance_charges_the_months_before_the_period(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session, p4x_init_balance=0, p4x_init_date=date(2024, 4, 1)
        )

        result = calculate_fee_balance(db_session, member, "2024-07-01", "2024-07-31")

        assert result is not None
        # April and May at 10, June at 15
        assert result["start_balance"] == Decimal(-35)
        assert result["end_balance"] == Decimal(-50)

    def test_period_crossing_a_year_end(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session, p4x_init_balance=0, p4x_init_date=date(2024, 11, 1)
        )

        result = calculate_fee_balance(db_session, member, "2024-11-01", "2025-02-28")

        assert result is not None
        bookings = [e["booking"] for e in result["progress"] if e["type"] == "fee"]
        assert bookings == ["2024-11-10", "2024-12-10", "2025-01-10", "2025-02-10"]
        assert result["end_balance"] == Decimal(-60)


class TestStepFunctionHelpers:
    @pytest.mark.parametrize(
        ("month", "expected"),
        [
            (date(2016, 12, 1), Decimal(0)),
            (date(2017, 1, 1), Decimal(10)),
            (date(2017, 1, 31), Decimal(10)),
            (date(2024, 5, 31), Decimal(10)),
            (date(2024, 6, 1), Decimal(15)),
            (date(2030, 1, 1), Decimal(15)),
        ],
    )
    def test_fee_at(self, month, expected):
        starts = [date(2017, 1, 1), date(2024, 6, 1)]
        fees = [Decimal(10), Decimal(15)]

        assert _fee_at(starts, fees, month) == expected

    def test_fee_at_without_any_fee(self):
        assert _fee_at([], [], date(2024, 1, 1)) == Decimal(0)

    def test_months_from_crosses_year_ends(self):
        assert _months_from(date(2024, 11, 1), 4) == [
            date(2024, 11, 1),
            date(2024, 12, 1),
            date(2025, 1, 1),
            date(2025, 2, 1),
        ]
        assert _months_from(date(2024, 1, 1), 0) == []
        assert len(_months_from(date(2017, 1, 1), 25)) == 25
        assert _months_from(date(2017, 1, 1), 25)[-1] == date(2019, 1, 1)

    def test_sum_over_a_fee_change(self):
        starts = [date(2024, 1, 1), date(2024, 3, 1)]
        fees = [Decimal("10.00"), Decimal("12.50")]

        assert _sum_fees_for_period(starts, fees, date(2024, 1, 1), 4) == Decimal(
            "45.00"
        )
        assert _sum_fees_for_period(starts, fees, date(2023, 11, 1), 4) == Decimal(
            "20.00"
        )
        assert _sum_fees_for_period(starts, fees, date(2024, 1, 1), 0) == Decimal(0)
