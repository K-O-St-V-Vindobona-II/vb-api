from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, status

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
    PartnerRef,
    TransactionResponse,
)
from app.services import p4x_account_service, p4x_category_service, p4x_partner_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def build_transaction_response(
    tx: P4xTransaction,
    db: Session,
) -> TransactionResponse:
    partner = None
    if tx.partner and tx.partner.deleted_at is None:
        entity = p4x_partner_service.find_partner_entity(
            db,
            tx.partner.partner_type,
            tx.partner.partner_id,
        )
        if entity:
            partner = PartnerRef(
                type=tx.partner.partner_type,
                id=tx.partner.partner_id,
                cn=getattr(entity, "cn", ""),
            )

    delegating_partner = None
    if tx.delegating_partner_type and tx.delegating_partner_id:
        entity = p4x_partner_service.find_partner_entity(
            db,
            tx.delegating_partner_type,
            tx.delegating_partner_id,
        )
        if entity:
            delegating_partner = PartnerRef(
                type=tx.delegating_partner_type,
                id=tx.delegating_partner_id,
                cn=getattr(entity, "cn", ""),
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

    filters = []
    for h in tx.category_filter_hits or []:
        cf = h.category_filter
        hit_count = (
            db.query(P4xCategoryFilterHit)
            .filter(
                P4xCategoryFilterHit.p4x_category_filter_id == cf.id_uuid,
            )
            .count()
        )
        filters.append(
            CategoryFilterShortResponse(
                id=cf.id,
                name=cf.name,
                p4x_account_id=cf.p4x_account_id,
                account_id=cf.account.id,
                p4x_account_label=cf.account.label if cf.account else None,
                iban=cf.iban,
                min_amount=cf.min_amount,
                max_amount=cf.max_amount,
                subject=cf.subject,
                subject_mode=cf.subject_mode,
                p4x_category_id=cf.p4x_category_id,
                hitCount=hit_count,
            )
        )

    return TransactionResponse(
        id=tx.id,
        booking=str(tx.booking) if tx.booking else None,
        valuation=str(tx.valuation) if tx.valuation else None,
        iban=tx.iban,
        amount=tx.amount,
        subject=tx.subject,
        # tx.account.id, not tx.p4x_account_id (which now stores id_uuid) -
        # TransactionTable.vue routes raw/attachment downloads by this
        # still-integer account id.
        p4x_account_id=tx.account.id,
        p4x_account_cn=tx.account.cn if tx.account else "",
        p4x_account_iban=tx.account.iban if tx.account else "",
        comment=tx.comment,
        has_attachment=tx.has_attachment,
        partner=partner,
        delegating_partner=delegating_partner,
        p4x_category_directs=directs,
        p4x_category_filters=filters,
    )


def build_account_response(
    db: Session,
    account: P4xAccount,
) -> AccountResponse:
    tx_count = (
        db.query(P4xTransaction)
        .filter(
            P4xTransaction.p4x_account_id == account.id_uuid,
            P4xTransaction.deleted_at.is_(None),
        )
        .count()
    )
    latest = (
        db.query(P4xTransaction.booking)
        .filter(
            P4xTransaction.p4x_account_id == account.id_uuid,
            P4xTransaction.deleted_at.is_(None),
        )
        .order_by(P4xTransaction.booking.desc())
        .first()
    )

    return AccountResponse(
        id=account.id,
        id_uuid=account.id_uuid,
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
    account_id: int,
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
    account_id: int,
    transaction_id: int,
) -> P4xTransaction:
    account = get_account_or_404(db, account_id)
    tx = (
        db.query(P4xTransaction)
        .filter(
            P4xTransaction.id == transaction_id,
            P4xTransaction.p4x_account_id == account.id_uuid,
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
        id_uuid=cat.id_uuid,
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
        account_id=f.account.id,
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
    filter_id: int,
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
    transaction_id: int,
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
    member_id: int,
) -> Member:
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Mitglied nicht gefunden.")
    return member


def get_category_or_404(
    db: Session,
    category_id: int,
) -> P4xCategory:
    cat = db.query(P4xCategory).filter(P4xCategory.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden.")
    return cat
