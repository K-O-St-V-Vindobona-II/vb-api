from datetime import UTC, date, datetime, timedelta

import bcrypt

from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_fee import P4xFee
from app.models.p4x_partner import P4xPartner
from app.models.p4x_transaction import P4xTransaction
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import create_user_session
from app.services.p4x_fee_balance_service import (
    _count_months,
    calculate_fee_balance,
    get_fee_balances,
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
    delegating_member_id: int | None = None,
) -> None:
    """Add a transaction that counts as a fee payment for this member -
    or, if delegating_member_id is set, a payment made via `member`'s own
    IBAN but explicitly delegated to a different member (see
    TestGetFeeBalancesPaymentAttribution)."""
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
        sha256_hash=f"fee_pay_{member.id}_{booking}_{amount}_{delegating_member_id}",
        booking=booking,
        valuation=booking,
        iban=iban,
        amount=amount,
        subject=f"MB {member.couleurname}",
        p4x_account_id=account.id,
        delegating_member_id=delegating_member_id,
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


class TestGetFeeBalances:
    """Bulk equivalent of get_debtors(), now listing every fee-liable
    member instead of only those with a negative balance ("Saldenliste"
    instead of "Schuldnerliste")."""

    def test_negative_balance_member_included(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2017, 1, 1),
        )

        balances = get_fee_balances(db_session)
        assert len(balances) > 0
        entry = next(b for b in balances if b["id"] == member.id)
        assert entry["end_balance"] < 0

    def test_positive_balance_member_included(self, db_session):
        """Semantic change vs. the old debtors-only endpoint: a member
        with a positive balance now APPEARS, instead of being filtered
        out."""
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=999999,
            p4x_init_date=date(2017, 1, 1),
        )

        balances = get_fee_balances(db_session)
        entry = next(b for b in balances if b["id"] == member.id)
        assert entry["end_balance"] > 0

    def test_freed_member_with_zero_balance_included(self, db_session):
        """Semantic change vs. the old endpoint: a freed member (never
        negative, since no fees are ever deducted) now APPEARS too,
        flagged via p4x_freed instead of being invisible."""
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=0,
        )

        balances = get_fee_balances(db_session)
        entry = next(b for b in balances if b["id"] == member.id)
        assert entry["end_balance"] == 0
        assert entry["p4x_freed"] is True

    def test_sorted_by_balance_ascending(self, db_session):
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

        balances = [e["end_balance"] for e in get_fee_balances(db_session)]
        assert balances == sorted(balances)

    def test_member_without_init_setup_excluded_from_list(self, db_session):
        _seed_base(db_session)
        setup_member = _create_fee_member(
            db_session, p4x_init_balance=0, email="setup@t.at", couleurname="Setup"
        )
        no_setup_member = _create_fee_member(
            db_session,
            p4x_init_date=None,
            p4x_init_balance=0,
            email="nosetup@t.at",
            couleurname="NoSetup",
        )
        no_setup_member.philistrierungsdatum = None
        db_session.commit()

        ids = {e["id"] for e in get_fee_balances(db_session)}
        assert setup_member.id in ids
        assert no_setup_member.id not in ids

    def test_freed_member_payments_still_counted(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=0,
            email="freedpay@t.at",
            couleurname="FreedPay",
        )
        _add_fee_payment(db_session, member, date(2017, 1, 15), 50.0)

        balances = get_fee_balances(db_session)
        entry = next(b for b in balances if b["id"] == member.id)
        assert entry["end_balance"] == 50.0


class TestGetFeeBalancesPaymentAttribution:
    """All cases here use p4x_freed=True members so the expected balance
    depends only on init_balance + attributed payments, never on the
    current date (no monthly fee accrual to account for)."""

    def test_payment_via_owned_iban_counts(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=0,
            email="owned@t.at",
            couleurname="Owned",
        )
        _add_fee_payment(db_session, member, date(2017, 1, 15), 50.0)

        entry = next(b for b in get_fee_balances(db_session) if b["id"] == member.id)
        assert entry["end_balance"] == 50.0

    def test_payment_via_delegation_counts_for_target_not_iban_owner(self, db_session):
        """Note: the target needs a P4xPartner row of their own (even
        unrelated to this payment) for delegation credit to apply at
        all - see _attribute_payment's docstring for the (replicated)
        reason why."""
        _seed_base(db_session)
        payer = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=0,
            email="payer@t.at",
            couleurname="Payer",
        )
        target = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=0,
            email="target@t.at",
            couleurname="Target",
        )
        db_session.add(
            P4xPartner(
                iban="DE-TARGET-OWN",
                member_id=target.id,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        db_session.commit()
        _add_fee_payment(
            db_session,
            payer,
            date(2017, 1, 15),
            50.0,
            delegating_member_id=target.id,
        )

        balances = {b["id"]: b["end_balance"] for b in get_fee_balances(db_session)}
        assert balances[target.id] == 50.0
        assert balances[payer.id] == 0.0

    def test_payment_before_init_date_excluded(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=0,
            p4x_init_date=date(2020, 1, 1),
            email="before@t.at",
            couleurname="Before",
        )
        _add_fee_payment(db_session, member, date(2019, 12, 31), 50.0)

        entry = next(b for b in get_fee_balances(db_session) if b["id"] == member.id)
        assert entry["end_balance"] == 0.0

    def test_payment_after_end_date_excluded(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=0,
            email="after@t.at",
            couleurname="After",
        )
        _add_fee_payment(db_session, member, date(2099, 1, 1), 50.0)

        entry = next(b for b in get_fee_balances(db_session) if b["id"] == member.id)
        assert entry["end_balance"] == 0.0

    def test_payment_exactly_on_init_date_counts(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=0,
            p4x_init_date=date(2020, 3, 1),
            email="oninit@t.at",
            couleurname="OnInit",
        )
        _add_fee_payment(db_session, member, date(2020, 3, 1), 50.0)

        entry = next(b for b in get_fee_balances(db_session) if b["id"] == member.id)
        assert entry["end_balance"] == 50.0

    def test_payment_exactly_on_end_date_counts(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=0,
            email="onend@t.at",
            couleurname="OnEnd",
        )
        end_date = datetime.now(UTC).date().replace(day=1) - timedelta(days=1)
        _add_fee_payment(db_session, member, end_date, 50.0)

        entry = next(b for b in get_fee_balances(db_session) if b["id"] == member.id)
        assert entry["end_balance"] == 50.0

    def test_member_with_multiple_ibans_all_count(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=0,
            email="multi@t.at",
            couleurname="Multi",
        )
        _add_fee_payment(db_session, member, date(2017, 1, 15), 30.0, iban="DE010")
        _add_fee_payment(db_session, member, date(2017, 2, 15), 20.0, iban="DE011")

        entry = next(b for b in get_fee_balances(db_session) if b["id"] == member.id)
        assert entry["end_balance"] == 50.0

    def test_member_without_any_partner_iban_no_crash(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=42,
            email="noiban@t.at",
            couleurname="NoIban",
        )

        entry = next(b for b in get_fee_balances(db_session) if b["id"] == member.id)
        assert entry["end_balance"] == 42.0


class TestGetFeeBalancesFeeRateChange:
    def test_fee_rate_change_reflected_in_balance(self, db_session):
        """Fees change from 10€ to 15€ in June 2024 (see _seed_base) -
        the bulk path has no date-override, so it's compared against
        calculate_fee_balance() for the same (also override-free) call
        instead of a hardcoded, 'today'-dependent number."""
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2024, 5, 1),
            email="ratechange@t.at",
            couleurname="RateChange",
        )

        entry = next(b for b in get_fee_balances(db_session) if b["id"] == member.id)
        expected = calculate_fee_balance(db_session, member)
        assert expected is not None
        assert entry["end_balance"] == expected["end_balance"]


class TestGetFeeBalancesEdgeCases:
    def test_member_joined_current_month_end_date_bumped(self, db_session):
        """Replicates calculate_fee_balance's end_date < start_date
        branch: a member whose init_date falls in the current (or a
        future) month gets end_date bumped to the end of that month,
        instead of the global default end_date (end of previous month,
        which would otherwise exclude this member's only due month)."""
        _seed_base(db_session)
        current_month_start = datetime.now(UTC).date().replace(day=1)
        member = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=current_month_start,
            email="currentmonth@t.at",
            couleurname="CurrentMonth",
        )

        entry = next(b for b in get_fee_balances(db_session) if b["id"] == member.id)
        expected = calculate_fee_balance(db_session, member)
        assert expected is not None
        assert entry["end_balance"] == expected["end_balance"]

    def test_freed_member_with_rate_change_still_zero_fees(self, db_session):
        _seed_base(db_session)
        member = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=0,
            p4x_init_date=date(2024, 5, 1),
            email="freedrate@t.at",
            couleurname="FreedRate",
        )

        entry = next(b for b in get_fee_balances(db_session) if b["id"] == member.id)
        assert entry["end_balance"] == 0.0


class TestGetFeeBalancesMatchesCalculateFeeBalance:
    """Strongest correctness guarantee: the bulk path must exactly match
    calculate_fee_balance(db, member) for every member, across a mix of
    scenarios (debtor, positive, freed+payment, multiple ibans,
    delegation, fee-rate change) - catches any future divergence between
    the two implementations automatically."""

    def test_bulk_matches_single_member_calls(self, db_session):
        _seed_base(db_session)
        debtor = _create_fee_member(
            db_session, p4x_init_balance=0, email="p-debtor@t.at", couleurname="D"
        )
        positive = _create_fee_member(
            db_session,
            p4x_init_balance=999999,
            email="p-positive@t.at",
            couleurname="P",
        )
        freed = _create_fee_member(
            db_session,
            p4x_freed=True,
            p4x_init_balance=50,
            email="p-freed@t.at",
            couleurname="F",
        )
        _add_fee_payment(db_session, freed, date(2017, 1, 15), 25.0)
        multi_iban = _create_fee_member(
            db_session, p4x_init_balance=0, email="p-multi@t.at", couleurname="M"
        )
        _add_fee_payment(db_session, multi_iban, date(2017, 1, 15), 10.0, iban="DE020")
        _add_fee_payment(db_session, multi_iban, date(2017, 2, 15), 10.0, iban="DE021")
        delegated_target = _create_fee_member(
            db_session, p4x_init_balance=0, email="p-target@t.at", couleurname="T"
        )
        payer = _create_fee_member(
            db_session, p4x_init_balance=0, email="p-payer@t.at", couleurname="PY"
        )
        _add_fee_payment(
            db_session,
            payer,
            date(2017, 1, 15),
            40.0,
            delegating_member_id=delegated_target.id,
        )
        rate_change = _create_fee_member(
            db_session,
            p4x_init_balance=0,
            p4x_init_date=date(2024, 5, 1),
            email="p-rate@t.at",
            couleurname="R",
        )

        members = [
            debtor,
            positive,
            freed,
            multi_iban,
            delegated_target,
            payer,
            rate_change,
        ]
        bulk_by_id = {e["id"]: e["end_balance"] for e in get_fee_balances(db_session)}

        for member in members:
            single = calculate_fee_balance(db_session, member)
            assert single is not None
            assert bulk_by_id[member.id] == single["end_balance"], member.couleurname


class TestGetFeeBalancesQueryCount:
    """N+1 regression guard (CLAUDE.md): the query count must stay
    constant as the number of fee members grows."""

    def test_query_count_does_not_scale_with_member_count(
        self, db_session, count_queries
    ):
        _seed_base(db_session)
        for i in range(3):
            member = _create_fee_member(
                db_session,
                p4x_init_balance=0,
                email=f"small{i}@t.at",
                couleurname=f"Small{i}",
            )
            _add_fee_payment(
                db_session, member, date(2017, 1, 15), 10.0, iban=f"DE-S-{i}"
            )

        with count_queries() as small:
            get_fee_balances(db_session)

        for i in range(30):
            member = _create_fee_member(
                db_session,
                p4x_init_balance=0,
                email=f"large{i}@t.at",
                couleurname=f"Large{i}",
            )
            _add_fee_payment(
                db_session, member, date(2017, 1, 15), 10.0, iban=f"DE-L-{i}"
            )

        with count_queries() as large:
            get_fee_balances(db_session)

        assert large.count == small.count
        assert small.count <= 9


def _seed_http_auth(db) -> None:
    db.add(Role(id="phil-xxxx", group="philchc", label="Phil-x", order=1))
    db.commit()


def _create_admin(db) -> Member:
    pw = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
    member = Member(
        vorname="Test",
        nachname="Admin",
        couleurname="Tester",
        email="admin@t.at",
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


def _login(db, member: Member) -> dict[str, str]:
    token, _, _ = create_user_session(db, member)
    return {"Authorization": f"Bearer {token}"}


class TestGetFeeBalancesViaHttpEndpoint:
    def test_endpoint_returns_balances_for_all_fee_members(self, db_session, client):
        _seed_base(db_session)
        _seed_http_auth(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        member = _create_fee_member(
            db_session, p4x_init_balance=0, email="http@t.at", couleurname="Http"
        )

        resp = client.get("/api/p4x/fee-balances", headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        ids = {row["id"] for row in data}
        assert member.id in ids
        row = next(r for r in data if r["id"] == member.id)
        assert set(row.keys()) == {"id", "cn", "p4x_freed", "balance"}

    def test_endpoint_requires_auth(self, db_session, client):
        _seed_base(db_session)
        resp = client.get("/api/p4x/fee-balances")
        assert resp.status_code == 401


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
