from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, extract, func
from sqlalchemy import false as sa_false
from sqlalchemy import true as sa_true
from sqlalchemy.orm import selectinload

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from app.schemas.p4x import AccountSaveRequest

from app.core.datetime_utils import local_today
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
from app.models.p4x_partner import P4xPartner
from app.models.p4x_transaction import P4xTransaction
from app.services import p4x_partner_service

PAGINATION_SIZE = 100


# ---------------------------------------------------------------------------
# Account CRUD
# ---------------------------------------------------------------------------


def create_account(db: Session, data: AccountSaveRequest) -> P4xAccount:
    existing = (
        db.query(P4xAccount)
        .filter(
            P4xAccount.iban == data.iban,
            P4xAccount.deleted_at.is_(None),
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="IBAN existiert bereits.",
        )

    now = datetime.now(UTC)
    account = P4xAccount(
        iban=data.iban,
        bic=data.bic,
        label=data.label,
        init_date=data.init_date,
        init_balance=data.init_balance,
        created_at=now,
        updated_at=now,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def update_account(
    db: Session,
    account: P4xAccount,
    data: AccountSaveRequest,
) -> P4xAccount:
    dup = (
        db.query(P4xAccount)
        .filter(
            P4xAccount.iban == data.iban,
            P4xAccount.id != account.id,
            P4xAccount.deleted_at.is_(None),
        )
        .first()
    )
    if dup:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="IBAN existiert bereits.",
        )

    account.iban = data.iban
    account.bic = data.bic
    account.label = data.label
    account.init_date = data.init_date
    account.init_balance = data.init_balance
    db.commit()
    db.refresh(account)
    return account


def delete_account(db: Session, account: P4xAccount) -> None:
    tx_count = (
        db.query(P4xTransaction)
        .filter(
            P4xTransaction.p4x_account_id == account.id,
            P4xTransaction.deleted_at.is_(None),
        )
        .count()
    )
    if tx_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Konto kann nicht gelöscht werden, da Transaktionen vorhanden sind.",
        )
    account.deleted_at = datetime.now(UTC)
    db.commit()


def get_active_accounts(db: Session) -> list[P4xAccount]:
    return db.query(P4xAccount).filter(P4xAccount.deleted_at.is_(None)).all()


# ---------------------------------------------------------------------------
# Account queries
# ---------------------------------------------------------------------------


def get_account_balance(
    db: Session,
    account: P4xAccount,
    up_to_date: date | None = None,
) -> Decimal:
    if up_to_date is None:
        up_to_date = local_today()

    total = (
        db.query(func.sum(P4xTransaction.amount))
        .filter(
            P4xTransaction.p4x_account_id == account.id,
            P4xTransaction.booking <= up_to_date,
            P4xTransaction.deleted_at.is_(None),
        )
        .scalar()
    ) or Decimal(0)

    return account.init_balance + total


# Eager-load the two lazy="select" relationships that
# p4x_response_builders.build_transaction_response() reads for every
# transaction it renders - without this, listing endpoints issue one extra
# query per relationship per row (N+1).
_TRANSACTION_RESPONSE_OPTIONS = (
    selectinload(P4xTransaction.category_directs),
    selectinload(P4xTransaction.category_filter_hits),
)


def get_transactions_by_month(
    db: Session,
    account: P4xAccount,
    year: int,
    month: int,
    page: int,
) -> tuple[list[P4xTransaction], int]:
    query = (
        db.query(P4xTransaction)
        .filter(
            P4xTransaction.p4x_account_id == account.id,
            P4xTransaction.deleted_at.is_(None),
            extract("year", P4xTransaction.booking) == year,
            extract("month", P4xTransaction.booking) == month,
        )
        .options(*_TRANSACTION_RESPONSE_OPTIONS)
        .order_by(P4xTransaction.booking.desc())
    )
    total = query.count()
    items = query.offset((page - 1) * PAGINATION_SIZE).limit(PAGINATION_SIZE).all()
    return items, total


def no_delegation_filter() -> ColumnElement[bool]:
    """True when a transaction has no delegating partner set at all."""
    return (
        P4xTransaction.delegating_member_id.is_(None)
        & P4xTransaction.delegating_contact_id.is_(None)
        & P4xTransaction.delegating_p4x_account_id.is_(None)
        & P4xTransaction.delegating_p4x_specialcontact_id.is_(None)
    )


def get_transactions_by_partner(
    db: Session,
    account: P4xAccount,
    partner_type: str,
    partner_id: uuid.UUID,
    page: int,
) -> tuple[list[P4xTransaction], int]:
    # Translates the partner_type/partner_id pair callers still use into the
    # matching exclusive-arc FK columns (see P4xPartner / P4xTransaction).
    if partner_type == "member":
        partner_column = P4xPartner.member_id
        delegating_column = P4xTransaction.delegating_member_id
    elif partner_type == "contact":
        partner_column = P4xPartner.contact_id
        delegating_column = P4xTransaction.delegating_contact_id
    elif partner_type == "account":
        partner_column = P4xPartner.p4x_account_id
        delegating_column = P4xTransaction.delegating_p4x_account_id
    elif partner_type == "special":
        partner_column = P4xPartner.p4x_specialcontact_id
        delegating_column = P4xTransaction.delegating_p4x_specialcontact_id
    else:
        msg = f"Unbekannter partner_type: {partner_type!r}"
        raise ValueError(msg)

    # p4x_transactions.delegating_* still stores the target's legacy
    # integer id - that table's own UUID cutover is a later slice -
    # resolved here via the same id_uuid identifier so both halves of the
    # OR below compare against the correct column type. If the identifier
    # matches no entity at all, the delegating half must contribute
    # nothing rather than falling back to an "IS NULL" comparison, which
    # would wrongly match every transaction with no delegating partner
    # set at all.
    remote = p4x_partner_service.find_partner_entity(db, partner_type, partner_id)
    delegating_match = (
        delegating_column == remote.id if remote is not None else sa_false()
    )

    partner_ibans = [
        r[0]
        for r in db.query(P4xPartner.iban)
        .filter(
            partner_column == partner_id,
            P4xPartner.deleted_at.is_(None),
        )
        .all()
    ]

    query = (
        db.query(P4xTransaction)
        .filter(
            P4xTransaction.p4x_account_id == account.id,
            P4xTransaction.deleted_at.is_(None),
        )
        .filter(
            # (partner matches AND no delegating) OR (delegating matches)
            (P4xTransaction.iban.in_(partner_ibans) & no_delegation_filter())
            | delegating_match
        )
        .options(*_TRANSACTION_RESPONSE_OPTIONS)
        .order_by(P4xTransaction.booking.desc())
    )
    total = query.count()
    items = query.offset((page - 1) * PAGINATION_SIZE).limit(PAGINATION_SIZE).all()
    return items, total


def get_transactions_by_category(
    db: Session,
    account: P4xAccount,
    category_id: int,
    page: int,
) -> tuple[list[P4xTransaction], int]:
    direct_tx_ids = {
        r[0]
        for r in db.query(P4xCategoryDirect.p4x_transaction_id)
        .filter(
            P4xCategoryDirect.p4x_category_id == category_id,
            P4xCategoryDirect.deleted_at.is_(None),
        )
        .all()
    }

    # p4x_category_filters.p4x_category_id now stores id_uuid - category_id
    # here is still the category's legacy integer id (that table's own
    # UUID cutover is a later slice), so it must be translated first.
    category_id_uuid = (
        db.query(P4xCategory.id_uuid).filter(P4xCategory.id == category_id).scalar()
    )
    filter_ids = [
        r[0]
        for r in db.query(P4xCategoryFilter.id)
        .filter(
            P4xCategoryFilter.p4x_category_id == category_id_uuid,
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
        .all()
    }
    filter_only_tx_ids = filter_tx_ids - all_direct_tx_ids
    all_tx_ids = direct_tx_ids | filter_only_tx_ids

    if not all_tx_ids:
        return [], 0

    query = (
        db.query(P4xTransaction)
        .filter(
            P4xTransaction.p4x_account_id == account.id,
            P4xTransaction.deleted_at.is_(None),
            P4xTransaction.id.in_(all_tx_ids),
        )
        .options(*_TRANSACTION_RESPONSE_OPTIONS)
        .order_by(P4xTransaction.booking.desc())
    )
    total = query.count()
    items = query.offset((page - 1) * PAGINATION_SIZE).limit(PAGINATION_SIZE).all()
    return items, total


def get_transactions_by_filter(
    db: Session,
    account: P4xAccount,
    filter_id: int,
    page: int,
) -> tuple[list[P4xTransaction], int]:
    hit_tx_ids = {
        r[0]
        for r in db.query(P4xCategoryFilterHit.p4x_transaction_id)
        .filter(
            P4xCategoryFilterHit.p4x_category_filter_id == filter_id,
        )
        .all()
    }

    all_direct_tx_ids = {
        r[0]
        for r in db.query(P4xCategoryDirect.p4x_transaction_id)
        .filter(
            P4xCategoryDirect.deleted_at.is_(None),
        )
        .all()
    }
    tx_ids = hit_tx_ids - all_direct_tx_ids

    if not tx_ids:
        return [], 0

    query = (
        db.query(P4xTransaction)
        .filter(
            P4xTransaction.p4x_account_id == account.id,
            P4xTransaction.deleted_at.is_(None),
            P4xTransaction.id.in_(tx_ids),
        )
        .options(*_TRANSACTION_RESPONSE_OPTIONS)
        .order_by(P4xTransaction.booking.desc())
    )
    total = query.count()
    items = query.offset((page - 1) * PAGINATION_SIZE).limit(PAGINATION_SIZE).all()
    return items, total


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def get_warnings_partner(
    db: Session,
    limit: int | None = None,
) -> tuple[list[P4xTransaction], int]:
    partner_ibans = {
        r[0]
        for r in db.query(P4xPartner.iban)
        .filter(
            P4xPartner.deleted_at.is_(None),
        )
        .all()
    }

    query = (
        db.query(P4xTransaction)
        .filter(
            P4xTransaction.deleted_at.is_(None),
            ~P4xTransaction.iban.in_(partner_ibans) if partner_ibans else sa_true(),
        )
        .options(*_TRANSACTION_RESPONSE_OPTIONS)
        .order_by(P4xTransaction.booking.desc())
    )
    total = query.count()
    items = query.limit(limit).all() if limit else query.all()
    return items, total


def get_warnings_category(
    db: Session,
    limit: int | None = None,
) -> tuple[list[P4xTransaction], int]:
    tx_with_directs = {
        r[0]
        for r in db.query(P4xCategoryDirect.p4x_transaction_id)
        .filter(
            P4xCategoryDirect.deleted_at.is_(None),
        )
        .distinct()
        .all()
    }

    all_tx = (
        db.query(P4xTransaction)
        .filter(P4xTransaction.deleted_at.is_(None))
        .options(*_TRANSACTION_RESPONSE_OPTIONS)
        .all()
    )

    warnings: list[P4xTransaction] = []
    for tx in all_tx:
        if tx.id in tx_with_directs:
            continue
        filter_count = (
            db.query(P4xCategoryFilterHit)
            .filter(P4xCategoryFilterHit.p4x_transaction_id == tx.id)
            .count()
        )
        if filter_count != 1:
            warnings.append(tx)

    total = len(warnings)
    warnings.sort(key=lambda t: t.booking or date.min, reverse=True)
    if limit:
        warnings = warnings[:limit]
    return warnings, total


# ---------------------------------------------------------------------------
# Account categories (which categories are used in an account)
# ---------------------------------------------------------------------------


def get_account_categories(db: Session, account: P4xAccount) -> list[P4xCategory]:
    tx_ids = [
        r[0]
        for r in db.query(P4xTransaction.id)
        .filter(
            P4xTransaction.p4x_account_id == account.id,
            P4xTransaction.deleted_at.is_(None),
        )
        .all()
    ]
    if not tx_ids:
        return []

    direct_cat_ids = {
        r[0]
        for r in db.query(P4xCategoryDirect.p4x_category_id)
        .filter(
            P4xCategoryDirect.p4x_transaction_id.in_(tx_ids),
            P4xCategoryDirect.deleted_at.is_(None),
        )
        .distinct()
        .all()
    }

    filter_ids = {
        r[0]
        for r in db.query(P4xCategoryFilterHit.p4x_category_filter_id)
        .filter(
            P4xCategoryFilterHit.p4x_transaction_id.in_(tx_ids),
        )
        .distinct()
        .all()
    }
    # p4x_category_filters.p4x_category_id now stores id_uuid, but
    # direct_cat_ids/all_cat_ids/P4xCategory.id below are all still the
    # category's legacy integer id - joining through P4xCategory here
    # (instead of selecting p4x_category_id directly) keeps this set in
    # the same id flavor as direct_cat_ids.
    filter_cat_ids = (
        {
            r[0]
            for r in db.query(P4xCategory.id)
            .join(
                P4xCategoryFilter,
                P4xCategoryFilter.p4x_category_id == P4xCategory.id_uuid,
            )
            .filter(
                P4xCategoryFilter.id.in_(filter_ids),
            )
            .distinct()
            .all()
        }
        if filter_ids
        else set()
    )

    all_cat_ids = direct_cat_ids | filter_cat_ids
    if not all_cat_ids:
        return []

    return db.query(P4xCategory).filter(P4xCategory.id.in_(all_cat_ids)).all()
