from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, TypedDict

from sqlalchemy import func

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from app.models.member import Member
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
from app.models.p4x_fee import P4xFee
from app.models.p4x_partner import P4xPartner
from app.models.p4x_transaction import P4xTransaction
from app.services import p4x_account_service

FEE_CATEGORY_ID = 1


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
    data: dict[str, str | Decimal | bool | None],
) -> None:
    init_date_raw = data["p4x_init_date"]
    if isinstance(init_date_raw, str):
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
    member_id: int,
    from_date: date,
    to_date: date,
    *,
    inclusive_end: bool = False,
) -> Decimal:
    """Get sum of fee payments for a member in a date range.

    Fee payments = byPartner('member', id)
    AND byCategory(FEE_CATEGORY_ID) AND amount > 0
    """
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
        | (P4xTransaction.delegating_member_id == member_id),
    )

    if inclusive_end:
        query = query.filter(P4xTransaction.booking <= to_date)
    else:
        query = query.filter(P4xTransaction.booking < to_date)

    result = query.scalar()
    return result or Decimal(0)


def _get_fee_payments_list(
    db: Session,
    member_id: int,
    from_date: date,
    to_date: date,
) -> list[dict[str, str | Decimal]]:
    """Get individual fee payments as list for the progress view."""
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
            | (P4xTransaction.delegating_member_id == member_id),
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
            prev_month = datetime.now(UTC).date().replace(day=1) - timedelta(days=1)
            end_date = prev_month
    else:
        prev_month = datetime.now(UTC).date().replace(day=1) - timedelta(days=1)
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
        member.id,
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
        _get_fee_payments_list(db, member.id, start_date, end_date),
    )

    end_balance = start_balance + sum(
        (Decimal(str(e["amount"])) for e in progress), start=Decimal(0)
    )

    progress.sort(key=lambda e: str(e["booking"]))

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
