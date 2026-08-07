from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.enums import FeeInterval
from app.models.member import Member
from app.models.org import Org
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_fee import P4xFee
from app.models.p4x_partner import P4xPartner
from app.models.p4x_transaction import P4xTransaction
from app.models.state import State
from app.services.p4x_fee_balance_service import (
    FEE_INTERVAL_CEILING_CUTOVER,
    calculate_fee_balance,
    get_debtors,
    is_fee_member,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _seed_base(db) -> None:
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Org(id="vbn", label="VBN", order=2),
            State(id="up", label="UP", order=1),
            State(id="fu", label="FU", order=2),
        ]
    )
    db.commit()

    db.add(
        P4xAccount(
            iban="AT942011100005301947",
            bic="GIBAATWWXXX",
            label="Girokonto",
            init_date=date(2015, 1, 1),
            init_balance=0,
            created_at=_now(),
            updated_at=_now(),
        )
    )
    db.commit()

    # FEE_CATEGORY_ID (= 1) is a hardcoded app-level assumption
    # (app/services/p4x_fee_balance_service.py) — the fee category must have id=1.
    db.add(
        P4xCategory(
            id=1,
            name="eingang.mitgliedsbeitrag",
            label="Mitgliedsbeitrag",
            background_color="#336600",
            text_color="#ffffff",
            created_at=_now(),
            updated_at=_now(),
        )
    )
    db.commit()

    db.add_all(
        [
            P4xFee(start=date(2017, 1, 1), fee=10.0, protected=True),
            P4xFee(start=date(2024, 6, 1), fee=15.0, protected=False),
        ]
    )
    db.commit()


def _create_fee_member(
    db,
    couleurname: str = "Kopernikus",
    email: str = "test@test.at",
    p4x_init_date: date | None = date(2017, 1, 1),
    p4x_init_balance: int = 36,
    p4x_freed: bool = False,
    org_id: str = "vbw",
    state_id: str = "up",
    p4x_fee_interval: FeeInterval = FeeInterval.MONTHLY,
) -> Member:
    member = Member(
        vorname="Test",
        nachname="User",
        couleurname=couleurname,
        email=email,
        auth_password="x",
        auth_locked=False,
        org_id=org_id,
        state_id=state_id,
        p4x_init_date=p4x_init_date,
        p4x_init_balance=p4x_init_balance,
        p4x_freed=p4x_freed,
        p4x_fee_interval=p4x_fee_interval,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def _add_fee_payment(
    db,
    member: Member,
    booking: date,
    amount: float,
    iban: str = "DE001",
) -> None:
    """Add a transaction that counts as a fee payment for this member."""
    account = db.query(P4xAccount).first()
    category = (
        db.query(P4xCategory)
        .filter(P4xCategory.name == "eingang.mitgliedsbeitrag")
        .first()
    )

    partner = db.query(P4xPartner).filter(P4xPartner.iban == iban).first()
    if not partner:
        partner = P4xPartner(
            iban=iban,
            member_id=member.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(partner)
        db.flush()

    tx = P4xTransaction(
        sha256_hash=f"fee_pay_{member.id}_{booking}_{amount}",
        booking=booking,
        valuation=booking,
        iban=iban,
        amount=amount,
        subject=f"MB {member.couleurname}",
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


class TestIsFeeMember:
    def test_valid_fee_member(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(db_session)
        assert is_fee_member(member) is True

    def test_wrong_org(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(db_session, org_id="vbn", email="wrong@t.at")
        assert is_fee_member(member) is False

    def test_wrong_state(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            state_id="fu",
            email="wrong@t.at",
        )
        assert is_fee_member(member) is False

    def test_entlassen(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(db_session, email="ent@t.at")
        member.entlassen = True
        db_session.commit()
        assert is_fee_member(member) is False


class TestFeeBalanceSimple:
    def test_no_payments_no_freed(self, db_session):
        """Member with init_balance=0, no payments, 12 months of fees at 10€."""
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2017, 1, 1),
        )

        balance = calculate_fee_balance(
            db_session,
            member,
            "2017-01-01",
            "2017-12-31",
        )
        assert balance is not None
        assert balance["start_date"] == "2017-01-01"
        assert balance["count"]["fees"] == 12
        assert balance["count"]["payments"] == 0
        assert balance["sum"]["fees"] == -120.0
        assert balance["end_balance"] == -120.0

    def test_freed_member_no_fees(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=0,
        )

        balance = calculate_fee_balance(
            db_session,
            member,
            "2017-01-01",
            "2017-12-31",
        )
        assert balance is not None
        assert balance["count"]["fees"] == 0
        assert balance["sum"]["fees"] == 0
        assert balance["end_balance"] == 0.0

    def test_not_fee_member_returns_none(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            org_id="vbn",
            email="nonfee@t.at",
        )
        assert calculate_fee_balance(db_session, member) is None

    def test_no_dates_returns_none(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_date=None,
            email="nodate@t.at",
        )
        member.philistrierungsdatum = None
        db_session.commit()
        assert calculate_fee_balance(db_session, member) is None


class TestFeeBalanceWithPayments:
    def test_fees_minus_payments(self, db_session):
        """3 months of fees (10€ each), 2 payments (15€ each)."""
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2017, 1, 1),
        )

        _add_fee_payment(db_session, member, date(2017, 1, 15), 15.0)
        _add_fee_payment(db_session, member, date(2017, 2, 15), 15.0)

        balance = calculate_fee_balance(
            db_session,
            member,
            "2017-01-01",
            "2017-03-31",
        )
        assert balance is not None
        assert balance["count"]["fees"] == 3
        assert balance["count"]["payments"] == 2
        assert balance["sum"]["fees"] == -30.0
        assert balance["sum"]["payments"] == 30.0
        assert balance["end_balance"] == 0.0

    def test_init_balance_included(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=100,
            p4x_init_date=date(2017, 1, 1),
        )

        balance = calculate_fee_balance(
            db_session,
            member,
            "2017-01-01",
            "2017-03-31",
        )
        assert balance is not None
        assert balance["start_balance"] == 100.0
        assert balance["end_balance"] == 100.0 - 30.0  # 3*10€ fees

    def test_progress_sorted_chronologically(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2017, 1, 1),
        )

        _add_fee_payment(db_session, member, date(2017, 2, 14), 15.0)

        balance = calculate_fee_balance(
            db_session,
            member,
            "2017-01-01",
            "2017-03-31",
        )
        bookings = [e["booking"] for e in balance["progress"]]
        assert bookings == sorted(bookings)

    def test_progress_running_balance(self, db_session):
        """Each progress entry carries a running balance ending at end_balance."""
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2017, 1, 1),
        )

        _add_fee_payment(db_session, member, date(2017, 2, 14), 15.0)

        balance = calculate_fee_balance(
            db_session,
            member,
            "2017-01-01",
            "2017-03-31",
        )
        assert balance is not None

        running = balance["start_balance"]
        for entry in balance["progress"]:
            running += entry["amount"]
            assert entry["balance"] == running

        assert balance["progress"][-1]["balance"] == balance["end_balance"]

    def test_fee_rate_change(self, db_session):
        """Fees change from 10€ to 15€ in June 2024."""
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2024, 5, 1),
        )

        balance = calculate_fee_balance(
            db_session,
            member,
            "2024-05-01",
            "2024-07-31",
        )
        assert balance is not None
        fees = [e for e in balance["progress"] if e["type"] == "fee"]
        assert len(fees) == 3
        assert fees[0]["amount"] == -10.0  # May 2024
        assert fees[1]["amount"] == -15.0  # June 2024
        assert fees[2]["amount"] == -15.0  # July 2024


class TestFeeBalanceStartBalance:
    def test_start_balance_includes_prior_fees_and_payments(self, db_session):
        """Start_balance includes prior activity when requesting after init."""
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2017, 1, 1),
        )

        _add_fee_payment(db_session, member, date(2017, 1, 15), 15.0)
        _add_fee_payment(db_session, member, date(2017, 2, 15), 15.0)

        balance = calculate_fee_balance(
            db_session,
            member,
            "2017-03-01",
            "2017-03-31",
        )
        assert balance is not None
        # start_balance = 0 (init) - 2*10 (Jan+Feb fees) + 2*15 (Jan+Feb payments) = 10
        assert balance["start_balance"] == 10.0
        # end_balance = 10 - 10 (March fee) = 0
        assert balance["end_balance"] == 0.0


class TestDebtors:
    def test_debtors_list(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2017, 1, 1),
        )

        debtors = get_debtors(db_session)
        assert len(debtors) > 0
        assert debtors[0]["id"] == member.id
        assert debtors[0]["balance"] < 0

    def test_no_debtors_when_positive_balance(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=999999,
            p4x_init_date=date(2017, 1, 1),
        )

        debtors = get_debtors(db_session)
        debtor_ids = [d["id"] for d in debtors]
        assert member.id not in debtor_ids

    def test_freed_member_not_debtor(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=0,
        )

        debtors = get_debtors(db_session)
        debtor_ids = [d["id"] for d in debtors]
        assert member.id not in debtor_ids

    def test_debtors_sorted_by_balance(self, db_session):
        _seed_base(db_session)
        _create_fee_member(
            db_session,
            p4x_init_balance=-100,
            email="a@t.at",
            couleurname="A",
        )
        _create_fee_member(
            db_session,
            p4x_init_balance=-500,
            email="b@t.at",
            couleurname="B",
        )

        debtors = get_debtors(db_session)
        balances = [d["balance"] for d in debtors]
        assert balances == sorted(balances)


class TestFeeBalanceEdgeCases:
    def test_invalid_start_date_string_falls_back_to_init(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2017, 1, 1),
        )

        balance = calculate_fee_balance(
            db_session,
            member,
            "not-a-date",
            "2017-03-31",
        )
        assert balance is not None
        assert balance["start_date"] == "2017-01-01"

    def test_invalid_end_date_string_falls_back_to_prev_month(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2017, 1, 1),
        )

        balance = calculate_fee_balance(
            db_session,
            member,
            "2017-01-01",
            "not-a-date",
        )
        assert balance is not None
        # Falls back to previous month from today
        assert balance["end_date"] is not None

    def test_start_date_before_init_date_clamped(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2020, 6, 1),
        )

        balance = calculate_fee_balance(
            db_session,
            member,
            "2017-01-01",
            "2020-12-31",
        )
        assert balance is not None
        # start_date clamped to init_date
        assert balance["start_date"] == "2020-06-01"

    def test_end_date_before_start_date_adjusts(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2026, 6, 1),
        )

        balance = calculate_fee_balance(
            db_session,
            member,
            "2026-06-01",
            "2026-01-01",
        )
        assert balance is not None
        # end_date adjusted to last day of start_date's month
        assert balance["end_date"] == "2026-06-30"

    def test_philistrierungsdatum_fallback(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_date=None,
            p4x_init_balance=0,
            email="phil@t.at",
        )
        member.philistrierungsdatum = date(2020, 1, 15)
        db_session.commit()

        balance = calculate_fee_balance(
            db_session,
            member,
            "2020-01-01",
            "2020-03-31",
        )
        assert balance is not None
        # init_date derived from philistrierungsdatum, floored to first of month
        assert balance["start_date"] == "2020-01-01"

    def test_december_end_date(self, db_session):
        """End date in December crosses year boundary correctly."""
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2017, 1, 1),
        )

        balance = calculate_fee_balance(
            db_session,
            member,
            "2017-12-01",
            "2017-12-31",
        )
        assert balance is not None
        assert balance["end_date"] == "2017-12-31"
        assert balance["count"]["fees"] == 1


class TestCutoverGuard:
    """Guards FEE_INTERVAL_CEILING_CUTOVER against being removed or
    backdated without understanding the consequences — see that constant's
    docstring in p4x_fee_balance_service.py. If this test class ever fails
    after touching the cutover constant, re-read that docstring (and the
    P4x fee/donation redesign plan it references) before "fixing" it: a
    real analysis found 15 of 61 members would have been retroactively
    worse off without it, 9 of them without warning."""

    def test_large_overpayment_before_cutover_is_fully_credited(self, db_session):
        """A payment far exceeding one month's fee, booked BEFORE the
        cutover, must be credited in full — exactly the historical behavior
        this feature deliberately never changes."""
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2017, 1, 1),
        )
        _add_fee_payment(db_session, member, date(2017, 1, 15), 200.0)

        balance = calculate_fee_balance(db_session, member, "2017-01-01", "2017-01-31")
        assert balance is not None
        assert [e for e in balance["progress"] if e["type"] == "excess"] == []
        assert balance["end_balance"] == 190.0  # 200 credited - 10 (Jan fee)

    def test_same_overpayment_after_cutover_is_capped(self, db_session):
        """The identical kind of overpayment, but booked ON/AFTER the
        cutover, must produce an 'excess' entry — this is the behavior the
        cutover exists to introduce."""
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2026, 9, 1),
        )
        # Booked before the day-10 Soll, so running_balance_before the
        # payment is exactly 0 — makes the expected ceiling/excess split
        # trivial to verify by hand.
        _add_fee_payment(db_session, member, date(2026, 9, 5), 200.0)

        balance = calculate_fee_balance(db_session, member, "2026-09-01", "2026-09-30")
        assert balance is not None
        excess_entries = [e for e in balance["progress"] if e["type"] == "excess"]
        assert len(excess_entries) == 1
        assert excess_entries[0]["amount"] == Decimal("185.00")  # 200 - 15 (ceiling)
        assert balance["end_balance"] == 0.0  # 15 credited - 15 (Sept fee)

    def test_new_member_after_cutover_is_capped_from_first_payment(self, db_session):
        """A member whose p4x_init_date is entirely after the cutover has,
        by construction, only payments with booking >= CUTOVER — the
        capping must apply from their very first payment, no implicit
        'transition period'."""
        _seed_base(db_session)
        assert date(2026, 10, 1) >= FEE_INTERVAL_CEILING_CUTOVER
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2026, 10, 1),
        )
        _add_fee_payment(db_session, member, date(2026, 10, 5), 100.0)

        balance = calculate_fee_balance(db_session, member, "2026-10-01", "2026-10-31")
        assert balance is not None
        excess_entries = [e for e in balance["progress"] if e["type"] == "excess"]
        assert len(excess_entries) == 1
        assert excess_entries[0]["amount"] == Decimal("85.00")  # 100 - 15 (ceiling)


class TestFeeIntervalCeiling:
    """Payments booked on/after the cutover, exercising the advance-payment
    ceiling per FeeInterval tier. All payments below are booked before the
    day-10 Soll of the same month, so running_balance_before is exactly 0 —
    makes the expected credited/excess split trivial to verify by hand."""

    def test_quarterly_exact_payment_no_excess(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2026, 9, 1),
            p4x_fee_interval=FeeInterval.QUARTERLY,
        )
        _add_fee_payment(db_session, member, date(2026, 9, 5), 45.0)  # 3 * 15

        balance = calculate_fee_balance(db_session, member, "2026-09-01", "2026-09-30")
        assert balance is not None
        assert [e for e in balance["progress"] if e["type"] == "excess"] == []
        assert balance["end_balance"] == 30.0  # 45 credited - 15 (Sept fee)

    def test_semiannual_exact_payment_no_excess(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2026, 9, 1),
            p4x_fee_interval=FeeInterval.SEMIANNUAL,
        )
        _add_fee_payment(db_session, member, date(2026, 9, 5), 90.0)  # 6 * 15

        balance = calculate_fee_balance(db_session, member, "2026-09-01", "2026-09-30")
        assert balance is not None
        assert [e for e in balance["progress"] if e["type"] == "excess"] == []
        assert balance["end_balance"] == 75.0  # 90 credited - 15 (Sept fee)

    def test_annual_exact_payment_no_excess(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2026, 9, 1),
            p4x_fee_interval=FeeInterval.ANNUAL,
        )
        _add_fee_payment(db_session, member, date(2026, 9, 5), 180.0)  # 12 * 15

        balance = calculate_fee_balance(db_session, member, "2026-09-01", "2026-09-30")
        assert balance is not None
        assert [e for e in balance["progress"] if e["type"] == "excess"] == []
        assert balance["end_balance"] == 165.0  # 180 credited - 15 (Sept fee)

    def test_overpayment_beyond_ceiling_produces_excess(self, db_session):
        """Monthly (default) member paying far more than one month's fee —
        the core scenario this whole feature exists to correctly handle."""
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2026, 9, 1),
        )
        _add_fee_payment(db_session, member, date(2026, 9, 5), 45.0)  # 3 months' worth

        balance = calculate_fee_balance(db_session, member, "2026-09-01", "2026-09-30")
        assert balance is not None
        excess = [e for e in balance["progress"] if e["type"] == "excess"]
        assert len(excess) == 1
        assert excess[0]["amount"] == Decimal("30.00")
        payments = [e for e in balance["progress"] if e["type"] == "payment"]
        assert payments[0]["amount"] == Decimal("15.00")

    def test_arrears_repayment_never_capped(self, db_session):
        """A member in arrears catching up in one lump sum must be credited
        in full — the ceiling only limits advancing further INTO credit,
        never repaying a deficit."""
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=-500,
            p4x_init_date=date(2026, 8, 1),
        )
        _add_fee_payment(db_session, member, date(2026, 9, 5), 400.0)

        balance = calculate_fee_balance(db_session, member, "2026-09-01", "2026-09-30")
        assert balance is not None
        assert [e for e in balance["progress"] if e["type"] == "excess"] == []

    def test_unlimited_never_produces_excess(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2026, 9, 1),
            p4x_fee_interval=FeeInterval.UNLIMITED,
        )
        _add_fee_payment(db_session, member, date(2026, 9, 5), 10_000.0)

        balance = calculate_fee_balance(db_session, member, "2026-09-01", "2026-09-30")
        assert balance is not None
        assert [e for e in balance["progress"] if e["type"] == "excess"] == []
        assert balance["end_balance"] == 9985.0  # 10000 credited - 15 (Sept fee)


class TestFeeRateChangeWithinInterval:
    def test_ceiling_uses_actual_monthly_rates_not_flat_multiplication(
        self, db_session
    ):
        """An annual payer whose prepayment interval straddles a rate
        change — the ceiling must be the true blended sum of actual monthly
        rates, not a flat `interval_months * rate_at_payment_date` in
        either direction (which would misprice this member either way)."""
        _seed_base(db_session)
        # A second rate change on top of _seed_base's 2024-06 one, so the
        # trailing 12 months ending September 2026 actually straddle it:
        # Oct 2025 - Feb 2026 at 15€ (5 months), Mar 2026 - Sep 2026 at 20€
        # (7 months) = 5*15 + 7*20 = 75 + 140 = 215.
        db_session.add(P4xFee(start=date(2026, 3, 1), fee=20.0, protected=False))
        db_session.commit()

        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2026, 9, 1),
            p4x_fee_interval=FeeInterval.ANNUAL,
        )
        _add_fee_payment(db_session, member, date(2026, 9, 5), 215.0)

        balance = calculate_fee_balance(db_session, member, "2026-09-01", "2026-09-30")
        assert balance is not None
        # A flat `12 * 20` (=240) would have accepted more than paid (no
        # excess, but wrongly implying headroom for 25 more); a flat
        # `12 * 15` (=180) would have wrongly flagged 35 as excess. Only the
        # month-by-month blended sum (215) matches exactly.
        assert [e for e in balance["progress"] if e["type"] == "excess"] == []
        payments = [e for e in balance["progress"] if e["type"] == "payment"]
        assert payments[0]["amount"] == Decimal("215.00")
        assert balance["end_balance"] == 195.0  # 215 credited - 20 (Sept fee, new rate)


class TestFeeIntervalSelfHealing:
    def test_correcting_interval_retroactively_fixes_balance(self, db_session):
        """calculate_fee_balance recomputes from scratch on every call — a
        wrongly-defaulted interval (monthly) that caused an 'excess' entry
        self-heals the instant the member is retagged, with no data
        migration or manual cleanup needed."""
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2026, 9, 1),
        )
        _add_fee_payment(db_session, member, date(2026, 9, 5), 45.0)  # 3 months' worth

        before = calculate_fee_balance(db_session, member, "2026-09-01", "2026-09-30")
        assert before is not None
        assert len([e for e in before["progress"] if e["type"] == "excess"]) == 1

        member.p4x_fee_interval = FeeInterval.QUARTERLY
        db_session.commit()

        after = calculate_fee_balance(db_session, member, "2026-09-01", "2026-09-30")
        assert after is not None
        assert [e for e in after["progress"] if e["type"] == "excess"] == []
        assert after["end_balance"] == 30.0


class TestFreedMemberFeeCategorizedPayment:
    def test_payment_to_freed_member_lands_entirely_as_excess(self, db_session):
        """A freed member owes nothing (no Soll entries ever generated for
        them), so the ceiling is forced to 0 regardless of their tagged
        interval — any 'Beitrag'-categorized payment they still receive
        after the cutover is, by construction, a donation."""
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=0,
            p4x_init_date=date(2026, 9, 1),
        )
        _add_fee_payment(db_session, member, date(2026, 9, 5), 50.0)

        balance = calculate_fee_balance(db_session, member, "2026-09-01", "2026-09-30")
        assert balance is not None
        assert balance["count"]["fees"] == 0  # freed: no Soll entries at all
        excess = [e for e in balance["progress"] if e["type"] == "excess"]
        assert len(excess) == 1
        assert excess[0]["amount"] == Decimal("50.00")
        assert balance["end_balance"] == 0.0


class TestFeeBalanceQueryCount:
    """Regression test for the N+1 fix: calculate_fee_balance used to call
    fee_for_month() (one query each) once per month across two separate
    Soll-generation loops, plus duplicated payment queries — now the whole
    function issues a fixed, small number of queries regardless of how many
    months/payments are in the requested range."""

    def test_query_count_does_not_scale_with_time_range(
        self, db_session, count_queries
    ):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2017, 1, 1),
        )
        _add_fee_payment(db_session, member, date(2018, 6, 15), 15.0)

        # Warm-up call, not measured: expire_on_commit=True means the first
        # attribute access on `member`/`fee` rows after the commits above
        # triggers a one-time refresh SELECT that has nothing to do with
        # calculate_fee_balance's own query count — would otherwise pollute
        # the comparison below with a session-warmup artifact.
        calculate_fee_balance(db_session, member, "2017-01-01", "2017-01-31")

        with count_queries() as short_range:
            calculate_fee_balance(db_session, member, "2017-01-01", "2017-12-31")

        with count_queries() as long_range:
            calculate_fee_balance(db_session, member, "2017-01-01", "2026-07-31")

        assert long_range.count == short_range.count
