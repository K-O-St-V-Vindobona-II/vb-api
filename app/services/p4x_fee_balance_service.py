from __future__ import annotations

from bisect import bisect_right
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, TypedDict

from app.models.enums import FEE_INTERVAL_MONTHS, FeeInterval
from app.models.member import Member
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
from app.models.p4x_fee import P4xFee
from app.models.p4x_partner import P4xPartner
from app.models.p4x_transaction import P4xTransaction
from app.services import p4x_account_service

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

FEE_CATEGORY_ID = 1

# ---------------------------------------------------------------------------
# Advance-payment ceiling cutover
# ---------------------------------------------------------------------------
#
# WARUM DIESES DATUM EXISTIERT — nicht ohne Rücksprache entfernen oder
# zurückdatieren:
#
# Vor diesem Datum verbuchte calculate_fee_balance jede als "Beitrag"
# kategorisierte Zahlung immer voll als Guthaben, egal wie weit sie über das
# aktuell Fällige hinausging — Überzahlung und bewusste Vorauszahlung waren
# nicht unterscheidbar. Ab diesem Datum gilt stattdessen eine Obergrenze pro
# Zahlung (Member.p4x_fee_interval * tatsächliche Monatsbeiträge über den
# Intervall-Zeitraum, siehe _trailing_fee_sum) — der Teil einer Zahlung
# darüber hinaus wird als "excess" ausgewiesen: zählt weiterhin normal als
# Beitrag in der Kategorisierung, aber nicht mehr im Beitragskonto-Saldo.
#
# Eine Analyse vom 2026-08 (P4x-Beitrags-/Spenden-Dilemma-Redesign) hat an
# den echten Produktionsdaten gezeigt: würde man diese Obergrenze rückwirkend
# auf die GESAMTE Historie anwenden (kein Cutover), hätten 15 von 61
# damaligen Beitragsmitgliedern einen SCHLECHTEREN Saldo gezeigt als vorher —
# 9 davon wären ohne Vorwarnung von "im Plus" auf "im Minus" gekippt. Das ist
# eine strukturelle Eigenschaft der Obergrenze (sie kann rückwirkend nur
# gleich oder schlechter machen, nie besser), keine vermeidbare
# Design-Schwäche.
#
# Wird dieses Datum entfernt oder zurückdatiert, wiederholt sich exakt dieses
# Problem für alle Zahlungen vor dem neuen Wert. Siehe
# tests/p4x/test_p4x_fee_balance.py::TestCutoverGuard, das genau das
# absichert und bei einer gedankenlosen Änderung fehlschlägt.
FEE_INTERVAL_CEILING_CUTOVER = date(2026, 9, 1)


class FeeProgressEntry(TypedDict):
    """One line of a member's fee-balance history. 'excess' entries never
    contribute to `balance` — they exist purely for transparency (the
    payment still counts as a full 'Beitrag' in the categorization engine
    and cash reports, but the excess amount doesn't affect the fee-account
    balance)."""

    type: Literal["fee", "payment", "excess"]
    booking: str
    amount: Decimal
    balance: Decimal


class FeeBalanceResult(TypedDict):
    """Typed result of calculate_fee_balance."""

    start_date: str
    start_balance: Decimal
    count: dict[str, int]
    sum: dict[str, Decimal]
    end_date: str
    end_balance: Decimal
    progress: list[FeeProgressEntry]
    fee_ceiling_cutover_date: str


class FeeMemberSearchResult(TypedDict):
    """Typed result of search_fee_members."""

    id: int
    label: str


# ---------------------------------------------------------------------------
# Fee config
# ---------------------------------------------------------------------------


def get_all_fees(db: Session) -> list[P4xFee]:
    return db.query(P4xFee).order_by(P4xFee.start).all()


def fee_for_month(db: Session, target_date: date) -> Decimal:
    """Returns the fee applicable for a given month (latest start <= target).

    Single-lookup public API, used outside this module (information_service,
    scheduler). calculate_fee_balance itself uses the in-memory
    _make_fee_lookup() instead to avoid one query per month/payment."""
    first_of_month = target_date.replace(day=1)
    result = (
        db.query(P4xFee.fee)
        .filter(P4xFee.start <= first_of_month)
        .order_by(P4xFee.start.desc())
        .first()
    )
    return result[0] if result else Decimal(0)


def _make_fee_lookup(fees: list[P4xFee]) -> Callable[[date], Decimal]:
    """In-memory equivalent of fee_for_month() — one query total per
    calculate_fee_balance call instead of one per month/payment (N+1 fix)."""
    ordered = sorted(fees, key=lambda f: f.start)
    starts = [f.start.replace(day=1) for f in ordered]
    amounts = [f.fee for f in ordered]

    def lookup(target: date) -> Decimal:
        idx = bisect_right(starts, target.replace(day=1)) - 1
        return amounts[idx] if idx >= 0 else Decimal(0)

    return lookup


def create_fee(
    db: Session,
    year: int,
    month: int,
    fee_amount: Decimal,
) -> tuple[P4xFee | None, str | None]:
    """Returns (fee, None) on success or (None, error_message) on failure."""
    start = date(year, month, 1)

    if start < datetime.now(UTC).date().replace(day=1):
        return None, "Startmonat muss aktueller Monat sein oder in der Zukunft liegen."

    existing = db.query(P4xFee).filter(P4xFee.start == start).first()
    if existing:
        return None, "Startmonat muss eindeutig sein."

    fee = P4xFee(start=start, fee=fee_amount, protected=False)
    db.add(fee)
    db.commit()
    return fee, None


def delete_fee(db: Session, start_str: str) -> str | None:
    """Returns error message or None on success."""
    start = date.fromisoformat(start_str[:10])
    fee = (
        db.query(P4xFee)
        .filter(
            P4xFee.start == start,
            P4xFee.protected == False,  # noqa: E712
        )
        .first()
    )
    if not fee:
        return "Eintrag nicht gefunden oder geschützt."
    db.delete(fee)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Fee members
# ---------------------------------------------------------------------------

FEE_MEMBER_FILTER = {
    "org_id": "vbw",
    "state_id": "up",
    "entlassen": False,
    "verstorben": False,
}


def is_fee_member(member: Member) -> bool:
    return all(
        getattr(member, attr) == value for attr, value in FEE_MEMBER_FILTER.items()
    )


def get_fee_members(db: Session) -> list[Member]:
    return (
        db.query(Member)
        .filter(
            Member.org_id == "vbw",
            Member.state_id == "up",
            Member.entlassen == False,  # noqa: E712
            Member.verstorben == False,  # noqa: E712
        )
        .all()
    )


def search_fee_members(db: Session, term: str) -> list[FeeMemberSearchResult]:
    if len(term) < 3:
        return []

    pattern = f"%{term}%"
    members = (
        db.query(Member)
        .filter(
            Member.org_id == "vbw",
            Member.state_id == "up",
            Member.entlassen == False,  # noqa: E712
            Member.verstorben == False,  # noqa: E712
            (Member.vorname.ilike(pattern))
            | (Member.nachname.ilike(pattern))
            | (Member.couleurname.ilike(pattern)),
        )
        .all()
    )
    return [{"id": m.id, "label": m.cn} for m in members]


def update_fee_member(
    db: Session,
    member: Member,
    data: dict[str, str | date | Decimal | bool | FeeInterval | None],
) -> None:
    init_date_raw = data["p4x_init_date"]
    if isinstance(init_date_raw, date):
        member.p4x_init_date = init_date_raw
    elif isinstance(init_date_raw, str):
        member.p4x_init_date = date.fromisoformat(init_date_raw[:10])
    else:
        member.p4x_init_date = None

    init_balance_raw = data["p4x_init_balance"]
    member.p4x_init_balance = (
        Decimal(str(init_balance_raw)) if init_balance_raw is not None else None
    )

    freed_raw = data["p4x_freed"]
    member.p4x_freed = bool(freed_raw) if freed_raw is not None else None

    comment_raw = data.get("p4x_comment")
    member.p4x_comment = str(comment_raw) if comment_raw is not None else None

    interval_raw = data.get("p4x_fee_interval")
    if isinstance(interval_raw, FeeInterval):
        member.p4x_fee_interval = interval_raw
    elif isinstance(interval_raw, str):
        member.p4x_fee_interval = FeeInterval(interval_raw)

    db.commit()


# ---------------------------------------------------------------------------
# Fee balance calculation (1:1 from Member::feeBalance() for payments before
# FEE_INTERVAL_CEILING_CUTOVER; advance-payment ceiling applies from there on)
# ---------------------------------------------------------------------------

# One raw ledger event before the payment-crediting decision has been made:
# (booking date, "fee" due or "payment" received, signed amount — fee
# amounts are already negative, matching FeeProgressEntry's convention).
RawFeeEvent = tuple[date, Literal["fee", "payment"], Decimal]


def _next_month(d: date) -> date:
    return date(d.year + (d.month // 12), d.month % 12 + 1, 1)


def _prev_month(d: date) -> date:
    return date(d.year - (1 if d.month == 1 else 0), (d.month - 2) % 12 + 1, 1)


def _trailing_fee_sum(
    booking: date,
    interval_months: int,
    fee_lookup: Callable[[date], Decimal],
) -> Decimal:
    """Sum of the actual monthly fee amounts over the `interval_months`
    calendar months ending at (and including) booking's month — NOT a flat
    `interval_months * fee_lookup(booking)`, which would misprice a member
    whose fee rate changed partway through their prepayment interval (see
    test_fee_rate_change for the same month-by-month principle applied to
    the "Soll" side)."""
    total = Decimal(0)
    month_cursor = booking.replace(day=1)
    for _ in range(interval_months):
        total += fee_lookup(month_cursor)
        month_cursor = _prev_month(month_cursor)
    return total


def _credited_amount(
    booking: date,
    amount: Decimal,
    running_balance_before: Decimal,
    interval_months: int | None,
    fee_lookup: Callable[[date], Decimal],
) -> Decimal:
    """How much of a fee-category payment counts toward the balance.
    Payments before FEE_INTERVAL_CEILING_CUTOVER, and members with
    `interval_months is None` (FeeInterval.UNLIMITED), are always credited
    in full. Otherwise capped at `interval_months` worth of actual monthly
    fees (see _trailing_fee_sum) above the running balance — arrears
    (`running_balance_before` already below the ceiling) are never capped,
    only advancing further into credit is."""
    if booking < FEE_INTERVAL_CEILING_CUTOVER or interval_months is None:
        return amount
    ceiling = _trailing_fee_sum(booking, interval_months, fee_lookup)
    headroom = max(ceiling - running_balance_before, Decimal(0))
    return max(Decimal(0), min(amount, headroom))


def _fetch_fee_payment_events(
    db: Session,
    member_id: int,
    from_date: date,
    to_date: date,
) -> list[tuple[date, Decimal]]:
    """Fee-category ('Beitrag') payment transactions for a member in
    [from_date, to_date] (both ends inclusive), sorted chronologically.

    Fee payments = byPartner('member', id) AND byCategory(FEE_CATEGORY_ID)
    AND amount > 0 — ported from the legacy PHP Member::feeBalance(). Direct
    category assignment always takes precedence over filter matches (same
    rule as the categorization engine itself, see p4x_category_service)."""
    partner_ibans = [
        r[0]
        for r in db.query(P4xPartner.iban)
        .filter(
            P4xPartner.member_id == member_id,
            P4xPartner.deleted_at.is_(None),
        )
        .all()
    ]

    if not partner_ibans:
        return []

    direct_tx_ids = {
        r[0]
        for r in db.query(P4xCategoryDirect.p4x_transaction_id)
        .filter(
            P4xCategoryDirect.p4x_category_id == FEE_CATEGORY_ID,
            P4xCategoryDirect.deleted_at.is_(None),
        )
        .all()
    }

    filter_ids = [
        r[0]
        for r in db.query(P4xCategoryFilter.id)
        .filter(
            P4xCategoryFilter.p4x_category_id == FEE_CATEGORY_ID,
        )
        .all()
    ]
    filter_tx_ids = (
        {
            r[0]
            for r in db.query(P4xCategoryFilterHit.p4x_transaction_id)
            .filter(
                P4xCategoryFilterHit.p4x_category_filter_id.in_(filter_ids),
            )
            .all()
        }
        if filter_ids
        else set()
    )

    all_direct_tx_ids = {
        r[0]
        for r in db.query(P4xCategoryDirect.p4x_transaction_id)
        .filter(
            P4xCategoryDirect.deleted_at.is_(None),
        )
        .distinct()
        .all()
    }
    fee_cat_tx_ids = direct_tx_ids | (filter_tx_ids - all_direct_tx_ids)

    if not fee_cat_tx_ids:
        return []

    rows = (
        db.query(P4xTransaction.booking, P4xTransaction.amount)
        .filter(
            P4xTransaction.deleted_at.is_(None),
            P4xTransaction.amount > 0,
            P4xTransaction.id.in_(fee_cat_tx_ids),
            P4xTransaction.booking >= from_date,
            P4xTransaction.booking <= to_date,
            (
                P4xTransaction.iban.in_(partner_ibans)
                & p4x_account_service.no_delegation_filter()
            )
            | (P4xTransaction.delegating_member_id == member_id),
        )
        .order_by(P4xTransaction.booking)
        .all()
    )
    return [(booking, amount) for booking, amount in rows]


def _build_fee_ledger(
    db: Session,
    member: Member,
    init_date: date,
    end_date: date,
    fee_lookup: Callable[[date], Decimal],
) -> list[FeeProgressEntry]:
    """Full chronological ledger (fee-due + payment/excess entries) from
    init_date through end_date, with the advance-payment ceiling applied to
    payments booked on/after FEE_INTERVAL_CEILING_CUTOVER.

    Single sequential walk shared by calculate_fee_balance for both the
    start_balance pre-window sum and the displayed progress list — both need
    the SAME chronological processing, because the ceiling depends on the
    running balance at the moment of each payment (which in turn depends on
    every fee-due and payment event before it), not a flat, order-independent
    sum. Every returned entry's `balance` is the true absolute balance at
    that point (including everything since init_date), so callers can slice
    the result by date without recomputing anything."""
    raw_events: list[RawFeeEvent] = []

    if not member.p4x_freed:
        current = init_date
        while current <= end_date:
            due_date = current.replace(day=10)
            raw_events.append((due_date, "fee", -fee_lookup(due_date)))
            current = _next_month(current)

    payment_events = _fetch_fee_payment_events(db, member.id, init_date, end_date)
    for booking, amount in payment_events:
        raw_events.append((booking, "payment", amount))

    raw_events.sort(key=lambda e: e[0])

    # UNLIMITED always wins (explicit, deliberate trust decision — see
    # FeeInterval docstring). Otherwise, a freed member owes nothing, so
    # there is no cadence to advance ahead of: force the ceiling to 0
    # rather than looking up their (irrelevant) tagged interval, so a
    # "Beitrag"-categorized payment to a freed member is correctly
    # recognized as a de facto donation instead of quietly banking one
    # interval's worth of credit that no future Soll will ever consume.
    interval_months = (
        None
        if member.p4x_fee_interval is FeeInterval.UNLIMITED
        else (
            0 if member.p4x_freed else FEE_INTERVAL_MONTHS.get(member.p4x_fee_interval)
        )
    )

    progress: list[FeeProgressEntry] = []
    running = member.p4x_init_balance or Decimal(0)

    for booking, kind, amount in raw_events:
        if kind == "fee":
            running += amount
            progress.append(
                {
                    "type": "fee",
                    "booking": str(booking),
                    "amount": amount,
                    "balance": running,
                }
            )
            continue

        credited = _credited_amount(
            booking, amount, running, interval_months, fee_lookup
        )
        running += credited
        progress.append(
            {
                "type": "payment",
                "booking": str(booking),
                "amount": credited,
                "balance": running,
            }
        )

        excess = amount - credited
        if excess > 0:
            progress.append(
                {
                    "type": "excess",
                    "booking": str(booking),
                    "amount": excess,
                    "balance": running,
                }
            )

    return progress


def _last_day_of_month(d: date) -> date:
    return _next_month(d.replace(day=1)) - timedelta(days=1)


def _default_fee_window_end() -> date:
    """Last day of the month before 'now' — the long-standing default end
    date when no end_date_str is given (or it's invalid)."""
    return datetime.now(UTC).date().replace(day=1) - timedelta(days=1)


def _resolve_fee_window(
    init_date: date,
    start_date_str: str | None,
    end_date_str: str | None,
) -> tuple[date, date]:
    """Resolves calculate_fee_balance's [start_date, end_date] window.
    Invalid date strings fall back to sensible defaults rather than
    raising — start_date to init_date, end_date to the last fully-closed
    month — matching the legacy PHP behavior this ports."""
    if start_date_str:
        try:
            start_date = date.fromisoformat(start_date_str[:10]).replace(day=1)
        except ValueError:
            start_date = init_date
    else:
        start_date = init_date
    start_date = max(start_date, init_date)

    if end_date_str:
        try:
            end_date = _last_day_of_month(date.fromisoformat(end_date_str[:10]))
        except ValueError:
            end_date = _default_fee_window_end()
    else:
        end_date = _default_fee_window_end()

    if end_date < start_date:
        end_date = _last_day_of_month(start_date)

    return start_date, end_date


def calculate_fee_balance(
    db: Session,
    member: Member,
    start_date_str: str | None = None,
    end_date_str: str | None = None,
) -> FeeBalanceResult | None:
    """Exact replication of Member::feeBalance() in PHP for payments before
    FEE_INTERVAL_CEILING_CUTOVER; advance-payment ceiling applies from that
    date onward (see the constant's docstring above)."""
    if not is_fee_member(member):
        return None

    if member.p4x_init_date is None and member.philistrierungsdatum is None:
        return None

    init_date = member.p4x_init_date or member.philistrierungsdatum
    if init_date is None:
        return None
    init_date = init_date.replace(day=1)

    start_date, end_date = _resolve_fee_window(init_date, start_date_str, end_date_str)

    fee_lookup = _make_fee_lookup(get_all_fees(db))
    full_ledger = _build_fee_ledger(db, member, init_date, end_date, fee_lookup)

    start_date_iso = str(start_date)
    pre_window = [e for e in full_ledger if e["booking"] < start_date_iso]
    progress = [e for e in full_ledger if e["booking"] >= start_date_iso]

    start_balance = (member.p4x_init_balance or Decimal(0)) + sum(
        (e["amount"] for e in pre_window if e["type"] != "excess"), start=Decimal(0)
    )
    end_balance = start_balance + sum(
        (e["amount"] for e in progress if e["type"] != "excess"), start=Decimal(0)
    )

    fee_entries = [e for e in progress if e["type"] == "fee"]
    payment_entries = [e for e in progress if e["type"] == "payment"]

    return {
        "start_date": str(start_date),
        "start_balance": start_balance,
        "count": {
            "fees": len(fee_entries),
            "payments": len(payment_entries),
        },
        "sum": {
            "fees": sum((e["amount"] for e in fee_entries), start=Decimal(0)),
            "payments": sum((e["amount"] for e in payment_entries), start=Decimal(0)),
        },
        "end_date": str(end_date),
        "end_balance": end_balance,
        "progress": progress,
        "fee_ceiling_cutover_date": str(FEE_INTERVAL_CEILING_CUTOVER),
    }


def get_debtors(db: Session) -> list[dict[str, int | str | Decimal]]:
    fee_members = get_fee_members(db)
    debtors: list[dict[str, int | str | Decimal]] = []

    for member in fee_members:
        balance = calculate_fee_balance(db, member)
        if balance and balance["end_balance"] < 0:
            debtors.append(
                {
                    "id": member.id,
                    "cn": member.cn,
                    "balance": balance["end_balance"],
                }
            )

    debtors.sort(key=lambda d: Decimal(str(d.get("balance", 0))))
    return debtors
