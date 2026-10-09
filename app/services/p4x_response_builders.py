from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from sqlalchemy import func

from app.models.member import Member
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
from app.models.p4x_transaction import P4xTransaction
from app.schemas.p4x import (
    AccountResponse,
    CategoryDirectResponse,
    CategoryFilterResponse,
    CategoryFilterShortResponse,
    CategoryWithUsageResponse,
    FeeBalanceCount,
    FeeBalanceResponse,
    FeeBalanceSum,
    FeeMemberResponse,
    FeeProgressEntry,
    FeeResponse,
    PartnerRef,
    TransactionResponse,
)
from app.services import (
    p4x_account_service,
    p4x_category_service,
    p4x_fee_balance_service,
    p4x_partner_service,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from app.models.p4x_fee import P4xFee


def _filter_hit_counts(db: Session, filter_ids: set[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Number of hits of each filter, in one query."""
    if not filter_ids:
        return {}
    rows = (
        db.query(P4xCategoryFilterHit.p4x_category_filter_id, func.count())
        .filter(P4xCategoryFilterHit.p4x_category_filter_id.in_(filter_ids))
        .group_by(P4xCategoryFilterHit.p4x_category_filter_id)
        .all()
    )
    return {row[0]: row[1] for row in rows}


def _partner_ref(
    partner_type: str | None,
    partner_id: uuid.UUID | None,
    entities: dict[tuple[str, uuid.UUID], p4x_partner_service.PartnerEntity],
) -> PartnerRef | None:
    if not partner_type or not partner_id:
        return None
    entity = entities.get((partner_type, partner_id))
    if entity is None:
        return None
    return PartnerRef(type=partner_type, id=partner_id, cn=getattr(entity, "cn", ""))


def _build_transaction_response(
    tx: P4xTransaction,
    entities: dict[tuple[str, uuid.UUID], p4x_partner_service.PartnerEntity],
    hit_counts: dict[uuid.UUID, int],
) -> TransactionResponse:
    partner = None
    if tx.partner and tx.partner.deleted_at is None:
        partner = _partner_ref(tx.partner.partner_type, tx.partner.partner_id, entities)
    delegating_partner = _partner_ref(
        tx.delegating_partner_type, tx.delegating_partner_id, entities
    )

    directs = [
        CategoryDirectResponse(
            id=d.id,
            p4x_category_id=d.p4x_category_id,
            amount=d.amount,
        )
        for d in (tx.category_directs or [])
        if d.deleted_at is None
    ]

    filters = [
        CategoryFilterShortResponse(
            id=cf.id,
            name=cf.name,
            p4x_account_id=cf.p4x_account_id,
            p4x_account_label=cf.account.label if cf.account else None,
            iban=cf.iban,
            min_amount=cf.min_amount,
            max_amount=cf.max_amount,
            subject=cf.subject,
            subject_mode=cf.subject_mode,
            p4x_category_id=cf.p4x_category_id,
            hitCount=hit_counts.get(cf.id, 0),
        )
        for cf in (h.category_filter for h in tx.category_filter_hits or [])
    ]

    return TransactionResponse(
        id=tx.id,
        booking=str(tx.booking) if tx.booking else None,
        valuation=str(tx.valuation) if tx.valuation else None,
        iban=tx.iban,
        amount=tx.amount,
        subject=tx.subject,
        p4x_account_id=tx.p4x_account_id,
        p4x_account_cn=tx.account.cn if tx.account else "",
        p4x_account_iban=tx.account.iban if tx.account else "",
        comment=tx.comment,
        has_attachment=tx.has_attachment,
        partner=partner,
        delegating_partner=delegating_partner,
        p4x_category_directs=directs,
        p4x_category_filters=filters,
    )


def build_transaction_responses(
    txs: Sequence[P4xTransaction],
    db: Session,
) -> list[TransactionResponse]:
    """Responses for a list of transactions with a constant number of
    queries: the partners are looked up once per partner type and the hit
    counts of all filters in one grouped query, instead of per transaction."""
    entities = p4x_partner_service.find_partner_entities(
        db,
        [ref for tx in txs for ref in p4x_partner_service.transaction_partner_refs(tx)],
    )
    hit_counts = _filter_hit_counts(
        db,
        {h.p4x_category_filter_id for tx in txs for h in tx.category_filter_hits or []},
    )
    return [_build_transaction_response(tx, entities, hit_counts) for tx in txs]


def build_transaction_response(
    tx: P4xTransaction,
    db: Session,
) -> TransactionResponse:
    return build_transaction_responses([tx], db)[0]


def build_account_response(
    db: Session,
    account: P4xAccount,
) -> AccountResponse:
    tx_count = (
        db.query(P4xTransaction)
        .filter(
            P4xTransaction.p4x_account_id == account.id,
            P4xTransaction.deleted_at.is_(None),
        )
        .count()
    )
    latest = (
        db.query(P4xTransaction.booking)
        .filter(
            P4xTransaction.p4x_account_id == account.id,
            P4xTransaction.deleted_at.is_(None),
        )
        .order_by(P4xTransaction.booking.desc())
        .first()
    )

    return AccountResponse(
        id=account.id,
        iban=account.iban,
        bic=account.bic,
        label=account.label,
        init_date=str(account.init_date) if account.init_date else None,
        init_balance=account.init_balance,
        balance=p4x_account_service.get_account_balance(db, account),
        transactions_count=tx_count,
        transactions_latest=str(latest[0]) if latest else None,
    )


def get_account_or_404(
    db: Session,
    account_id: uuid.UUID,
) -> P4xAccount:
    account = (
        db.query(P4xAccount)
        .filter(
            P4xAccount.id == account_id,
            P4xAccount.deleted_at.is_(None),
        )
        .first()
    )
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Konto nicht gefunden.",
        )
    return account


def get_transaction_for_account(
    db: Session,
    account_id: uuid.UUID,
    transaction_id: uuid.UUID,
) -> P4xTransaction:
    account = get_account_or_404(db, account_id)
    tx = (
        db.query(P4xTransaction)
        .filter(
            P4xTransaction.id == transaction_id,
            P4xTransaction.p4x_account_id == account.id,
            P4xTransaction.deleted_at.is_(None),
        )
        .first()
    )
    if not tx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaktion nicht gefunden.",
        )
    return tx


def build_category_response(
    db: Session,
    cat: P4xCategory,
) -> CategoryWithUsageResponse:
    usage = p4x_category_service.get_category_usage(db, cat)
    return CategoryWithUsageResponse(
        id=cat.id,
        name=cat.name,
        label=cat.label,
        background_color=cat.background_color,
        text_color=cat.text_color,
        protected=cat.protected,
        used=usage,
    )


def build_filter_response(
    db: Session,
    f: P4xCategoryFilter,
) -> CategoryFilterResponse:
    return CategoryFilterResponse(
        id=f.id,
        name=f.name,
        p4x_account_id=f.p4x_account_id,
        p4x_account_label=f.account.label if f.account else None,
        iban=f.iban,
        min_amount=f.min_amount,
        max_amount=f.max_amount,
        subject=f.subject,
        subject_mode=f.subject_mode,
        p4x_category_id=f.p4x_category_id,
        hitCount=p4x_category_service.get_filter_hit_count(db, f),
    )


def get_filter_or_404(
    db: Session,
    filter_id: uuid.UUID,
) -> P4xCategoryFilter:
    f = (
        db.query(P4xCategoryFilter)
        .filter(
            P4xCategoryFilter.id == filter_id,
        )
        .first()
    )
    if not f:
        raise HTTPException(status_code=404, detail="Filter nicht gefunden.")
    return f


def get_transaction_or_404(
    db: Session,
    transaction_id: uuid.UUID,
) -> P4xTransaction:
    tx = (
        db.query(P4xTransaction)
        .filter(
            P4xTransaction.id == transaction_id,
            P4xTransaction.deleted_at.is_(None),
        )
        .first()
    )
    if not tx:
        raise HTTPException(status_code=404, detail="Transaktion nicht gefunden.")
    return tx


def get_member_or_404(
    db: Session,
    member_id: uuid.UUID,
) -> Member:
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Mitglied nicht gefunden.")
    return member


def get_category_or_404(
    db: Session,
    category_id: uuid.UUID,
) -> P4xCategory:
    cat = db.query(P4xCategory).filter(P4xCategory.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden.")
    return cat


def build_refreshed_transaction_response(
    db: Session, tx: P4xTransaction
) -> TransactionResponse:
    """Reload a transaction that a service just changed and build its response."""
    db.refresh(tx)
    return build_transaction_response(tx, db)


def update_transaction_and_build_response(
    db: Session,
    tx: P4xTransaction,
    comment: str | None,
    file_bytes: bytes | None,
    delete_attachment: bool,  # noqa: FBT001
) -> TransactionResponse:
    """Apply a comment/attachment edit and return the refreshed transaction."""
    p4x_partner_service.update_transaction_meta(
        db, tx, comment, file_bytes, delete_attachment
    )
    return build_refreshed_transaction_response(db, tx)


def build_fee_response(fee: P4xFee) -> FeeResponse:
    return FeeResponse(
        start=str(fee.start.replace(day=1)),
        fee=fee.fee,
        protected=bool(fee.protected),
    )


def build_fee_member_response(
    db: Session,
    member: Member,
) -> FeeMemberResponse:
    balance_data = p4x_fee_balance_service.calculate_fee_balance(db, member)

    balance = None
    if balance_data:
        balance = FeeBalanceResponse(
            start_date=balance_data["start_date"],
            start_balance=balance_data["start_balance"],
            count=FeeBalanceCount(
                fees=balance_data["count"]["fees"],
                payments=balance_data["count"]["payments"],
            ),
            sum=FeeBalanceSum(
                fees=balance_data["sum"]["fees"],
                payments=balance_data["sum"]["payments"],
            ),
            end_date=balance_data["end_date"],
            end_balance=balance_data["end_balance"],
            progress=[
                FeeProgressEntry(
                    type=str(p["type"]),
                    booking=str(p["booking"]),
                    amount=Decimal(str(p["amount"])),
                    balance=Decimal(str(p["balance"])),
                )
                for p in balance_data["progress"]
            ],
        )

    init_date_raw = member.p4x_init_date or member.philistrierungsdatum
    init_date_str: str | None = None
    if init_date_raw:
        init_date_str = str(init_date_raw.replace(day=1))

    return FeeMemberResponse(
        id=member.id,
        cn=member.cn,
        p4x_init_date=init_date_str,
        p4x_init_balance=member.p4x_init_balance,
        p4x_freed=bool(member.p4x_freed),
        p4x_comment=member.p4x_comment,
        balance=balance,
    )


def build_refreshed_fee_member_response(
    db: Session, member: Member
) -> FeeMemberResponse:
    """Reload a member that a service just changed and build its fee response."""
    db.refresh(member)
    return build_fee_member_response(db, member)
