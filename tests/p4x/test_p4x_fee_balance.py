from datetime import UTC, date, datetime

from app.models.member import Member
from app.models.org import Org
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_fee import P4xFee
from app.models.p4x_partner import P4xPartner
from app.models.p4x_transaction import P4xTransaction
from app.models.state import State
from app.services.p4x_service import (
    _count_months,
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
    # (app/services/p4x_service.py) — the fee category must have id=1.
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
        sha256hash=f"fee_pay_{member.id}_{booking}_{amount}",
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


class TestCountMonths:
    def test_same_month(self):
        assert _count_months(date(2026, 1, 1), date(2026, 1, 31)) == 1

    def test_two_months(self):
        assert _count_months(date(2026, 1, 1), date(2026, 2, 28)) == 2

    def test_full_year(self):
        assert _count_months(date(2026, 1, 1), date(2026, 12, 31)) == 12

    def test_across_years(self):
        assert _count_months(date(2017, 1, 1), date(2017, 12, 31)) == 12

    def test_partial_month(self):
        assert _count_months(date(2026, 1, 1), date(2026, 1, 15)) == 1

    def test_zero_months_when_end_before_start(self):
        result = _count_months(date(2026, 3, 1), date(2025, 12, 31))
        assert result <= 0


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
