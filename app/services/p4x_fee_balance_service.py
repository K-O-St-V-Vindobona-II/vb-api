from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple, TypedDict

from sqlalchemy import func, literal

if TYPE_CHECKING:
    import uuid

    from sqlalchemy import ColumnElement
    from sqlalchemy.orm import Session

from app.core.datetime_utils import local_today
from app.models.member import Member
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
from app.models.p4x_fee import P4xFee
from app.models.p4x_partner import P4xPartner
from app.models.p4x_transaction import P4xTransaction
from app.services import p4x_account_service
from app.services.search_utils import build_prefix_tsquery_text

FEE_CATEGORY_ID = 1


def _fee_category_id_uuid(db: Session) -> uuid.UUID | None:
    """Bridges the still-integer FEE_CATEGORY_ID constant to
    p4x_category_filters.p4x_category_id, which now stores id_uuid -
    a temporary lookup until slice 26 replaces this whole literal-id
    landmine with a name-based category lookup."""
    return (
        db.query(P4xCategory.id_uuid).filter(P4xCategory.id == FEE_CATEGORY_ID).scalar()
    )


class FeeBalanceResult(TypedDict):
    """Typed result of calculate_fee_balance."""

    start_date: str
    start_balance: Decimal
    count: dict[str, int]
    sum: dict[str, Decimal]
    end_date: str
    end_balance: Decimal
    progress: list[dict[str, str | Decimal]]


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
    """Returns the fee applicable for a given month (latest start <= target)."""
    first_of_month = target_date.replace(day=1)
    result = (
        db.query(P4xFee.fee)
        .filter(P4xFee.start <= first_of_month)
        .order_by(P4xFee.start.desc())
        .first()
    )
    return result[0] if result else Decimal(0)


def create_fee(
    db: Session,
    year: int,
    month: int,
    fee_amount: Decimal,
) -> tuple[P4xFee | None, str | None]:
    """Returns (fee, None) on success or (None, error_message) on failure."""
    start = date(year, month, 1)

    if start < local_today().replace(day=1):
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


# Every fee-member query below shares this exact filter set - fee tracking
# only ever applies to vbw/up (Altherrenschaft), and exempts departed
# (entlassen) or deceased (verstorben) members, regardless of match stage.
def _fee_member_base_filter() -> tuple[ColumnElement[bool], ...]:
    return (
        Member.org_id == "vbw",
        Member.state_id == "up",
        Member.entlassen == False,  # noqa: E712
        Member.verstorben == False,  # noqa: E712
    )


def _search_fee_members_exact(
    db: Session, tsquery: object
) -> list[tuple[Member, float]]:
    rank = func.ts_rank(Member.search_vector, tsquery)
    rows = (
        db.query(Member, rank)
        .filter(*_fee_member_base_filter(), Member.search_vector.op("@@")(tsquery))
        .order_by(rank.desc())
        .all()
    )
    return [(m, float(r)) for m, r in rows]


def _search_fee_members_fuzzy(db: Session, term: str) -> list[tuple[Member, float]]:
    """Typo-tolerant fallback (pg_trgm), name fields only - org/state are
    already a hard filter below, not part of the query text, so the
    org-code fuzzy-matching concern from standesdb_service's equivalent
    doesn't apply here."""
    q = literal(term)
    rank = func.greatest(
        func.word_similarity(term, func.coalesce(Member.vorname, "")),
        func.word_similarity(term, func.coalesce(Member.nachname, "")),
        func.word_similarity(term, func.coalesce(Member.couleurname, "")),
    )
    rows = (
        db.query(Member, rank)
        .filter(
            *_fee_member_base_filter(),
            (
                q.op("<%")(func.coalesce(Member.vorname, ""))
                | q.op("<%")(func.coalesce(Member.nachname, ""))
                | q.op("<%")(func.coalesce(Member.couleurname, ""))
            ),
        )
        .order_by(rank.desc())
        .all()
    )
    return [(m, float(r)) for m, r in rows]


def search_fee_members(db: Session, term: str) -> list[FeeMemberSearchResult]:
    """Search fee-liable (vbw/up) members by name, minimum 3 characters.
    Same two-stage exact/prefix + pg_trgm-fuzzy shape as
    standesdb_service.search_members_and_contacts(), see its docstring.
    """
    if len(term) < 3:
        return []

    tsquery_text = build_prefix_tsquery_text(db, term)
    ranked: list[tuple[float, FeeMemberSearchResult]] = []
    if tsquery_text:
        tsquery = func.to_tsquery("german", tsquery_text)
        ranked = [
            (r, {"id": m.id, "label": m.cn})
            for m, r in _search_fee_members_exact(db, tsquery)
        ]

    if not ranked:
        ranked = [
            (r, {"id": m.id, "label": m.cn})
            for m, r in _search_fee_members_fuzzy(db, term)
        ]

    ranked.sort(key=lambda entry: entry[0], reverse=True)
    return [result for _, result in ranked]


def update_fee_member(
    db: Session,
    member: Member,
    data: dict[str, str | date | Decimal | bool | None],
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

    db.commit()


# ---------------------------------------------------------------------------
# Fee balance calculation (1:1 from Member::feeBalance)
# ---------------------------------------------------------------------------


def _count_months(start_date: date, end_date: date) -> int:
    """Exact replication of Member::countMonths() in PHP.

    PHP: $start_date->firstOfMonth()->diff($end_date->lastOfMonth())
    Then: $diff->y * 12 + $diff->m + $diff->d / 30, rounded.
    """
    first = start_date.replace(day=1)
    if end_date.month == 12:
        last = date(end_date.year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(end_date.year, end_date.month + 1, 1) - timedelta(days=1)

    # Manual relativedelta: years, months, days between first and last
    years = last.year - first.year
    months = last.month - first.month
    days = last.day - first.day

    if days < 0:
        months -= 1
        # Days in the previous month of 'last'
        prev_month = last.replace(day=1) - timedelta(days=1)
        days += prev_month.day

    if months < 0:
        years -= 1
        months += 12

    total = years * 12 + months + days / 30
    return round(total)


def _get_fee_payments_sum(
    db: Session,
    member: Member,
    from_date: date,
    to_date: date,
    *,
    inclusive_end: bool = False,
) -> Decimal:
    """Get sum of fee payments for a member in a date range.

    Fee payments = byPartner('member', id)
    AND byCategory(FEE_CATEGORY_ID) AND amount > 0

    Both P4xPartner.member_id and P4xTransaction.delegating_member_id
    reference members.id_uuid, not members.id - members' own
    Final-Cutover is a later slice.
    """
    partner_ibans = [
        r[0]
        for r in db.query(P4xPartner.iban)
        .filter(
            P4xPartner.member_id == member.id_uuid,
            P4xPartner.deleted_at.is_(None),
        )
        .all()
    ]

    if not partner_ibans:
        return Decimal(0)

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
            P4xCategoryFilter.p4x_category_id == _fee_category_id_uuid(db),
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
        return Decimal(0)

    query = db.query(func.sum(P4xTransaction.amount)).filter(
        P4xTransaction.deleted_at.is_(None),
        P4xTransaction.amount > 0,
        P4xTransaction.id.in_(fee_cat_tx_ids),
        P4xTransaction.booking >= from_date,
        (
            P4xTransaction.iban.in_(partner_ibans)
            & p4x_account_service.no_delegation_filter()
        )
        | (P4xTransaction.delegating_member_id == member.id_uuid),
    )

    if inclusive_end:
        query = query.filter(P4xTransaction.booking <= to_date)
    else:
        query = query.filter(P4xTransaction.booking < to_date)

    result = query.scalar()
    return result or Decimal(0)


def _get_fee_payments_list(
    db: Session,
    member: Member,
    from_date: date,
    to_date: date,
) -> list[dict[str, str | Decimal]]:
    """Get individual fee payments as list for the progress view.

    Same id_uuid reasoning as _get_fee_payments_sum() above.
    """
    partner_ibans = [
        r[0]
        for r in db.query(P4xPartner.iban)
        .filter(
            P4xPartner.member_id == member.id_uuid,
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
            P4xCategoryFilter.p4x_category_id == _fee_category_id_uuid(db),
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

    txs = (
        db.query(P4xTransaction)
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
            | (P4xTransaction.delegating_member_id == member.id_uuid),
        )
        .all()
    )

    return [
        {
            "type": "payment",
            "booking": str(tx.booking),
            "amount": tx.amount,
        }
        for tx in txs
    ]


def calculate_fee_balance(  # noqa: C901, PLR0912, PLR0915
    db: Session,
    member: Member,
    start_date_str: str | None = None,
    end_date_str: str | None = None,
) -> FeeBalanceResult | None:
    """Exact replication of Member::feeBalance() in PHP."""
    if not is_fee_member(member):
        return None

    if member.p4x_init_date is None and member.philistrierungsdatum is None:
        return None

    init_date = member.p4x_init_date or member.philistrierungsdatum
    if init_date is None:
        return None
    init_date = init_date.replace(day=1)

    # Determine start_date
    if start_date_str:
        try:
            start_date = date.fromisoformat(start_date_str[:10]).replace(day=1)
        except ValueError:
            start_date = init_date
    else:
        start_date = init_date

    start_date = max(start_date, init_date)

    # Determine end_date
    if end_date_str:
        try:
            parsed_end = date.fromisoformat(end_date_str[:10])
            if parsed_end.month == 12:
                end_date = date(parsed_end.year + 1, 1, 1) - timedelta(days=1)
            else:
                end_date = date(parsed_end.year, parsed_end.month + 1, 1) - timedelta(
                    days=1
                )
        except ValueError:
            prev_month = local_today().replace(day=1) - timedelta(days=1)
            end_date = prev_month
    else:
        prev_month = local_today().replace(day=1) - timedelta(days=1)
        end_date = prev_month

    if end_date < start_date:
        if start_date.month == 12:
            end_date = date(start_date.year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(start_date.year, start_date.month + 1, 1) - timedelta(
                days=1
            )

    # Calculate start_balance
    start_balance = member.p4x_init_balance or Decimal(0)

    if not member.p4x_freed:
        prev_month_date = start_date.replace(day=1) - timedelta(days=1)
        n_months = _count_months(init_date, prev_month_date)
        for i in range(n_months):
            if init_date.month + i % 12 <= 12:
                year = init_date.year + (init_date.month - 1 + i) // 12
                month = (init_date.month - 1 + i) % 12 + 1
            else:
                year = init_date.year + (init_date.month - 1 + i) // 12
                month = (init_date.month - 1 + i) % 12 + 1
            current = date(year, month, 10)
            start_balance -= fee_for_month(db, current)

    start_balance += _get_fee_payments_sum(
        db,
        member,
        init_date,
        start_date,
        inclusive_end=False,
    )

    # Build progress
    progress: list[dict[str, str | Decimal]] = []

    if not member.p4x_freed:
        n_months = _count_months(start_date, end_date)
        for i in range(n_months):
            year = start_date.year + (start_date.month - 1 + i) // 12
            month = (start_date.month - 1 + i) % 12 + 1
            current = date(year, month, 10)
            fee_amount = fee_for_month(db, current)
            progress.append(
                {
                    "type": "fee",
                    "booking": str(current),
                    "amount": -fee_amount,
                }
            )

    progress.extend(
        _get_fee_payments_list(db, member, start_date, end_date),
    )

    end_balance = start_balance + sum(
        (Decimal(str(e["amount"])) for e in progress), start=Decimal(0)
    )

    progress.sort(key=lambda e: str(e["booking"]))

    running_balance = start_balance
    for entry in progress:
        running_balance += Decimal(str(entry["amount"]))
        entry["balance"] = running_balance

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
            "fees": sum(
                (Decimal(str(e["amount"])) for e in fee_entries), start=Decimal(0)
            ),
            "payments": sum(
                (Decimal(str(e["amount"])) for e in payment_entries),
                start=Decimal(0),
            ),
        },
        "end_date": str(end_date),
        "end_balance": end_balance,
        "progress": progress,
    }


# ---------------------------------------------------------------------------
# Bulk fee balance list ("Saldenliste") - N+1-safe equivalent of calling
# calculate_fee_balance(db, member) for every fee member and reading
# end_balance. calculate_fee_balance() itself and its helpers
# (_get_fee_payments_sum/_get_fee_payments_list) are deliberately left
# untouched here - they back the well-tested single-member views/exports
# and a related redesign of that function is intentionally on hold (see
# feature/p4x-fee-interval-cutover). The functions below duplicate a small
# amount of their logic on purpose to keep that surface completely
# unaffected, in exchange for a bulk path with a constant query count
# instead of one that scales with the number of fee members.
# ---------------------------------------------------------------------------


class FeeBalanceListEntry(TypedDict):
    """One row of the bulk fee-balance list. `end_balance` is
    mathematically identical to calculate_fee_balance(db, member)
    ["end_balance"] for the same member with default (no-override) dates
    - see TestGetFeeBalancesMatchesCalculateFeeBalance."""

    id: int
    cn: str
    p4x_freed: bool
    end_balance: Decimal


@dataclass
class _MemberFeeState:
    """Per-member working state accumulated during the bulk pass."""

    init_date: date
    end_date: date
    fee_sum: Decimal = Decimal(0)
    payment_sum: Decimal = Decimal(0)


class _FeeCategoryTxRow(NamedTuple):
    """One fee-category transaction row, as needed for bulk payment
    attribution (see _attribute_payment)."""

    iban: str | None
    booking: date
    amount: Decimal
    delegating_member_id: uuid.UUID | None
    no_delegation: bool


def _end_of_month(target: date) -> date:
    """Last calendar day of target's month."""
    if target.month == 12:
        return date(target.year + 1, 1, 1) - timedelta(days=1)
    return date(target.year, target.month + 1, 1) - timedelta(days=1)


def _sum_fees_for_period(
    month_starts: list[date],
    month_fees: list[Decimal],
    first_month: date,
    n_months: int,
) -> Decimal:
    """Pure-Python replication of the fee_for_month() step function - no
    DB calls. month_starts/month_fees come from get_all_fees(db), loaded
    once for the whole bulk run (see get_fee_balances)."""
    total = Decimal(0)
    for i in range(n_months):
        year = first_month.year + (first_month.month - 1 + i) // 12
        month = (first_month.month - 1 + i) % 12 + 1
        idx = bisect_right(month_starts, date(year, month, 1)) - 1
        if idx >= 0:
            total += month_fees[idx]
    return total


def _build_member_states(
    fee_members: list[Member],
    month_starts: list[date],
    month_fees: list[Decimal],
    default_end_date: date,
) -> dict[int, _MemberFeeState]:
    """One entry per fee-eligible member with a usable init_date. Members
    without p4x_init_date/philistrierungsdatum are silently skipped
    (mirrors calculate_fee_balance's `return None` for them)."""
    states: dict[int, _MemberFeeState] = {}
    for member in fee_members:
        init_date_raw = member.p4x_init_date or member.philistrierungsdatum
        if init_date_raw is None:
            continue
        init_date = init_date_raw.replace(day=1)
        end_date = (
            default_end_date
            if default_end_date >= init_date
            else _end_of_month(init_date)
        )
        fee_sum = Decimal(0)
        if not member.p4x_freed:
            n_months = _count_months(init_date, end_date)
            fee_sum = _sum_fees_for_period(
                month_starts, month_fees, init_date, n_months
            )
        states[member.id] = _MemberFeeState(
            init_date=init_date, end_date=end_date, fee_sum=fee_sum
        )
    return states


def _fee_category_tx_ids(db: Session) -> set[int]:
    """Member-independent set of transaction ids in the fee category.
    Deliberately duplicated from _get_fee_payments_sum/
    _get_fee_payments_list rather than extracted from them - those
    helpers back calculate_fee_balance() and are out of scope for
    refactoring here."""
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
        .filter(P4xCategoryFilter.p4x_category_id == _fee_category_id_uuid(db))
        .all()
    ]
    filter_tx_ids = (
        {
            r[0]
            for r in db.query(P4xCategoryFilterHit.p4x_transaction_id)
            .filter(P4xCategoryFilterHit.p4x_category_filter_id.in_(filter_ids))
            .all()
        }
        if filter_ids
        else set()
    )
    all_direct_tx_ids = {
        r[0]
        for r in db.query(P4xCategoryDirect.p4x_transaction_id)
        .filter(P4xCategoryDirect.deleted_at.is_(None))
        .distinct()
        .all()
    }
    return direct_tx_ids | (filter_tx_ids - all_direct_tx_ids)


def _iban_to_member_id_map(
    db: Session, id_by_uuid: dict[uuid.UUID, int]
) -> dict[str, int]:
    """One query for every fee member's own IBANs, instead of one query
    per member. P4xPartner.member_id references members.id_uuid (not
    members.id - members' own Final-Cutover is a later slice), so the
    lookup goes via id_uuid and translates back to each member's
    still-integer id via id_by_uuid, for the caller's
    dict[int, _MemberFeeState] keys."""
    if not id_by_uuid:
        return {}
    rows = (
        db.query(P4xPartner.iban, P4xPartner.member_id)
        .filter(
            P4xPartner.member_id.in_(id_by_uuid.keys()),
            P4xPartner.deleted_at.is_(None),
        )
        .all()
    )
    return {r[0]: id_by_uuid[r[1]] for r in rows if r[0] is not None}


def _fetch_fee_category_transactions(
    db: Session,
    tx_ids: set[int],
    booking_from: date,
    booking_to: date,
) -> list[_FeeCategoryTxRow]:
    """One query for every relevant transaction across all members.
    booking_from/booking_to are the tightest bounds covering every
    member's [init_date, end_date] window - a correctness-neutral
    optimization to avoid pulling years of irrelevant transactions; the
    exact per-member window is still enforced in _attribute_payment."""
    if not tx_ids:
        return []
    rows = (
        db.query(
            P4xTransaction.iban,
            P4xTransaction.booking,
            P4xTransaction.amount,
            P4xTransaction.delegating_member_id,
            p4x_account_service.no_delegation_filter(),
        )
        .filter(
            P4xTransaction.deleted_at.is_(None),
            P4xTransaction.amount > 0,
            P4xTransaction.id.in_(tx_ids),
            P4xTransaction.booking >= booking_from,
            P4xTransaction.booking <= booking_to,
        )
        .all()
    )
    return [
        _FeeCategoryTxRow(
            iban=r[0],
            booking=r[1],
            amount=r[2],
            delegating_member_id=r[3],
            no_delegation=bool(r[4]),
        )
        for r in rows
    ]


def _attribute_payment(
    states: dict[int, _MemberFeeState],
    iban_map: dict[str, int],
    members_with_own_iban: set[int],
    id_by_uuid: dict[uuid.UUID, int],
    row: _FeeCategoryTxRow,
) -> None:
    """Same attribution rule as p4x_account_service.no_delegation_filter()
    plus its call site in get_transactions_by_partner(): an explicit
    delegation always wins regardless of iban; otherwise iban ownership
    only applies when no delegation is set at all.

    Deliberate quirk, replicated for exact parity with
    _get_fee_payments_sum/_get_fee_payments_list: those helpers return
    early with zero payments for any member with NO P4xPartner row at
    all, BEFORE even checking delegating_member_id - so a member without
    a single registered iban of their own never receives credit for a
    payment, not even one explicitly delegated to them. See
    TestGetFeeBalancesMatchesCalculateFeeBalance's "T" (delegated_target)
    case, which caught this.

    row.delegating_member_id is the target member's id_uuid; id_by_uuid
    translates it back to the member's legacy integer id, the flavor
    states/members_with_own_iban are keyed by."""
    target_id = (
        id_by_uuid.get(row.delegating_member_id)
        if row.delegating_member_id is not None
        else None
    )
    if target_id is not None and target_id not in members_with_own_iban:
        target_id = None
    if target_id is None and row.no_delegation and row.iban is not None:
        target_id = iban_map.get(row.iban)
    if target_id is None:
        return
    state = states.get(target_id)
    if state is None:
        return
    if not (state.init_date <= row.booking <= state.end_date):
        return
    state.payment_sum += row.amount


def get_fee_balances(db: Session) -> list[FeeBalanceListEntry]:
    """Bulk equivalent of calling calculate_fee_balance(db, member) for
    every fee member and reading end_balance - with a constant number of
    queries regardless of member count (see TestGetFeeBalancesQueryCount
    and TestGetFeeBalancesMatchesCalculateFeeBalance)."""
    fee_members = get_fee_members(db)
    fees = get_all_fees(db)
    month_starts = [f.start.replace(day=1) for f in fees]
    month_fees = [f.fee for f in fees]
    default_end_date = local_today().replace(day=1) - timedelta(days=1)

    states = _build_member_states(
        fee_members, month_starts, month_fees, default_end_date
    )
    if not states:
        return []

    id_by_uuid = {m.id_uuid: m.id for m in fee_members if m.id in states}
    iban_map = _iban_to_member_id_map(db, id_by_uuid)
    members_with_own_iban = set(iban_map.values())
    booking_from = min(s.init_date for s in states.values())
    booking_to = max(s.end_date for s in states.values())
    tx_rows = _fetch_fee_category_transactions(
        db, _fee_category_tx_ids(db), booking_from, booking_to
    )
    for row in tx_rows:
        _attribute_payment(states, iban_map, members_with_own_iban, id_by_uuid, row)

    entries: list[FeeBalanceListEntry] = [
        {
            "id": member.id,
            "cn": member.cn,
            "p4x_freed": bool(member.p4x_freed),
            "end_balance": (
                (member.p4x_init_balance or Decimal(0))
                - states[member.id].fee_sum
                + states[member.id].payment_sum
            ),
        }
        for member in fee_members
        if member.id in states
    ]
    entries.sort(key=lambda e: e["end_balance"])
    return entries
