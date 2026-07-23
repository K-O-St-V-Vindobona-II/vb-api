import base64
import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.api.auth_guards import require_permission
from app.db.database import get_db
from app.models.member import Member
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
from app.models.p4x_fee import P4xFee
from app.models.p4x_transaction import P4xTransaction
from app.schemas.p4x import (
    AccountResponse,
    AccountSaveRequest,
    CategoryDirectResponse,
    CategoryFilterResponse,
    CategoryFilterSaveRequest,
    CategoryFilterShortResponse,
    CategoryResponse,
    CategorySaveRequest,
    CategoryWithUsageResponse,
    DashboardResponse,
    DebtorResponse,
    FeeBalanceCount,
    FeeBalanceResponse,
    FeeBalanceSum,
    FeeCreateRequest,
    FeeMemberResponse,
    FeeMemberUpdateRequest,
    FeeProgressEntry,
    FeeResponse,
    FilterHitResponse,
    ImportGiven,
    ImportResult,
    PaginatedTransactions,
    PartnerRef,
    PartnerSearchResult,
    SetPartnerRequest,
    SummaryOrderRequest,
    SumUpBalanceResponse,
    TransactionRawResponse,
    TransactionResponse,
    WarningsResponse,
)
from app.services import p4x_service

p4x_router = APIRouter()

PREVIEW_LIMIT = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_transaction_response(
    tx: P4xTransaction,
    db: Session,
) -> TransactionResponse:
    partner = None
    if tx.partner and tx.partner.deleted_at is None:
        entity = _find_partner_entity(
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
        entity = _find_partner_entity(
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
                P4xCategoryFilterHit.p4x_category_filter_id == cf.id,
            )
            .count()
        )
        filters.append(
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


def _build_account_response(
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
        balance=p4x_service.get_account_balance(db, account),
        transactions_count=tx_count,
        transactions_latest=str(latest[0]) if latest else None,
    )


def _find_partner_entity(
    db: Session,
    partner_type: str,
    partner_id: int,
) -> object | None:
    return p4x_service.find_partner_entity(db, partner_type, partner_id)


def _get_account_or_404(
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


def _get_transaction_for_account(
    db: Session,
    account_id: int,
    transaction_id: int,
) -> P4xTransaction:
    tx = (
        db.query(P4xTransaction)
        .filter(
            P4xTransaction.id == transaction_id,
            P4xTransaction.p4x_account_id == account_id,
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


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@p4x_router.get("/accounts")
def get_dashboard(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
) -> DashboardResponse:
    """Return all bank accounts with balances, categories, and warning counts."""
    accounts = (
        db.query(P4xAccount)
        .filter(
            P4xAccount.deleted_at.is_(None),
        )
        .all()
    )

    partner_items, partner_count = p4x_service.get_warnings_partner(
        db,
        limit=PREVIEW_LIMIT,
    )
    category_items, category_count = p4x_service.get_warnings_category(
        db,
        limit=PREVIEW_LIMIT,
    )

    categories = db.query(P4xCategory).all()

    return DashboardResponse(
        accounts=[_build_account_response(db, a) for a in accounts],
        warnings_partner=WarningsResponse(
            count=partner_count,
            preview=[_build_transaction_response(tx, db) for tx in partner_items],
        ),
        warnings_category=WarningsResponse(
            count=category_count,
            preview=[_build_transaction_response(tx, db) for tx in category_items],
        ),
        categories=[
            CategoryResponse(
                id=c.id,
                name=c.name,
                label=c.label,
                background_color=c.background_color,
                text_color=c.text_color,
                protected=c.protected,
            )
            for c in categories
        ],
    )


# ---------------------------------------------------------------------------
# Warnings (paginated detail endpoints)
# ---------------------------------------------------------------------------


@p4x_router.get("/warnings/partner")
def get_warnings_partner_list(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
    page: int = 1,
) -> PaginatedTransactions:
    """List transactions missing a partner assignment (paginated)."""
    items, total = p4x_service.get_warnings_partner(db)
    start = (page - 1) * p4x_service.PAGINATION_SIZE
    end = start + p4x_service.PAGINATION_SIZE
    page_items = items[start:end]
    return PaginatedTransactions(
        items=[_build_transaction_response(tx, db) for tx in page_items],
        total=total,
        page=page,
        per_page=p4x_service.PAGINATION_SIZE,
    )


@p4x_router.get("/warnings/category")
def get_warnings_category_list(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
    page: int = 1,
) -> PaginatedTransactions:
    """List transactions missing a category (paginated)."""
    items, total = p4x_service.get_warnings_category(db)
    start = (page - 1) * p4x_service.PAGINATION_SIZE
    end = start + p4x_service.PAGINATION_SIZE
    page_items = items[start:end]
    return PaginatedTransactions(
        items=[_build_transaction_response(tx, db) for tx in page_items],
        total=total,
        page=page,
        per_page=p4x_service.PAGINATION_SIZE,
    )


# ---------------------------------------------------------------------------
# Account CRUD
# ---------------------------------------------------------------------------


@p4x_router.post("/admin/accounts")
def create_account(
    data: AccountSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> AccountResponse:
    """Create a new bank account."""
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
    return _build_account_response(db, account)


@p4x_router.put("/admin/accounts/{account_id}")
def update_account(
    account_id: int,
    data: AccountSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> AccountResponse:
    """Update bank account details."""
    account = _get_account_or_404(db, account_id)

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
    return _build_account_response(db, account)


@p4x_router.delete("/admin/accounts/{account_id}")
def delete_account(
    account_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> dict[str, str]:
    """Delete a bank account."""
    account = _get_account_or_404(db, account_id)

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
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@p4x_router.post("/admin/accounts/{account_id}/import")
async def import_transactions(
    account_id: int,
    file: UploadFile,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> ImportResult | dict[str, object]:
    """Import bank transactions from a CSV file."""
    account = _get_account_or_404(db, account_id)

    iban_clean = account.iban.replace(" ", "")
    if iban_clean not in (file.filename or "").replace(" ", ""):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Der Name des Upload-Files enthält nicht die IBAN des Kontos.",
        )

    content = await file.read()
    if len(content) > 3 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Datei darf maximal 3 MB groß sein.",
        )

    raw_json = content.decode("utf-8")
    parse_result = p4x_service.parse_george_json(account.bic or "", raw_json)

    if not parse_result.success:
        return ImportResult(
            given=ImportGiven(p4x_account_id=account.id, parsed=False),
            message=parse_result.message,
        )

    original_structs = json.loads(raw_json)
    summary = p4x_service.import_transactions(
        db,
        account,
        parse_result.entries,
        original_structs,
    )

    p4x_service.apply_all_category_filters(db)

    db.refresh(account)
    account_data = _build_account_response(db, account)

    return {
        "given": {"p4x_account_id": account.id, "parsed": True},
        "summary": summary,
        "account": account_data.model_dump(),
    }


# ---------------------------------------------------------------------------
# Transactions by month / partner / category
# ---------------------------------------------------------------------------


@p4x_router.get(
    "/accounts/{account_id}/transactions/by-month/{year}/{month}",
)
def get_transactions_by_month(
    account_id: int,
    year: int,
    month: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
    page: int = 1,
) -> dict[str, list[TransactionResponse] | int | Decimal]:
    """List transactions for a specific month with start/end balances (paginated)."""
    account = _get_account_or_404(db, account_id)
    items, total = p4x_service.get_transactions_by_month(
        db,
        account,
        year,
        month,
        page,
    )

    given = date(year, month, 10)
    last_of_prev = given.replace(day=1) - timedelta(days=1)
    if month == 12:
        last_of_month = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_of_month = date(year, month + 1, 1) - timedelta(days=1)

    return {
        "items": [_build_transaction_response(tx, db) for tx in items],
        "total": total,
        "page": page,
        "per_page": p4x_service.PAGINATION_SIZE,
        "startbalance": p4x_service.get_account_balance(db, account, last_of_prev),
        "endbalance": p4x_service.get_account_balance(db, account, last_of_month),
    }


@p4x_router.get(
    "/accounts/{account_id}/transactions/by-partner/{partner_type}/{partner_id}",
)
def get_transactions_by_partner(
    account_id: int,
    partner_type: str,
    partner_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
    page: int = 1,
) -> PaginatedTransactions:
    """List transactions for a specific partner (paginated)."""
    account = _get_account_or_404(db, account_id)
    items, total = p4x_service.get_transactions_by_partner(
        db,
        account,
        partner_type,
        partner_id,
        page,
    )
    return PaginatedTransactions(
        items=[_build_transaction_response(tx, db) for tx in items],
        total=total,
        page=page,
        per_page=p4x_service.PAGINATION_SIZE,
    )


@p4x_router.get(
    "/accounts/{account_id}/transactions/by-category/{category_id}",
)
def get_transactions_by_category(
    account_id: int,
    category_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
    page: int = 1,
) -> PaginatedTransactions:
    """List transactions assigned to a specific category (paginated)."""
    account = _get_account_or_404(db, account_id)
    items, total = p4x_service.get_transactions_by_category(
        db,
        account,
        category_id,
        page,
    )
    return PaginatedTransactions(
        items=[_build_transaction_response(tx, db) for tx in items],
        total=total,
        page=page,
        per_page=p4x_service.PAGINATION_SIZE,
    )


# ---------------------------------------------------------------------------
# Transaction raw data and attachment
# ---------------------------------------------------------------------------


@p4x_router.get(
    "/accounts/{account_id}/transactions/raw/{transaction_id}",
)
def get_transaction_raw(
    account_id: int,
    transaction_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
) -> TransactionRawResponse:
    """Return the raw imported CSV data for a transaction."""
    tx = _get_transaction_for_account(db, account_id, transaction_id)
    return TransactionRawResponse(raw=tx.raw)


@p4x_router.get(
    "/accounts/{account_id}/transactions/attachment/{transaction_id}",
)
def get_transaction_attachment(
    account_id: int,
    transaction_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
) -> StreamingResponse:
    """Download the PDF attachment of a transaction."""
    tx = _get_transaction_for_account(db, account_id, transaction_id)
    if not tx.has_attachment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kein Anhang vorhanden.",
        )

    if not tx.attachment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kein Anhang vorhanden.",
        )
    pdf_bytes = base64.b64decode(tx.attachment)
    filename = f"Beilage_{tx.id}.pdf"

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Partner search
# ---------------------------------------------------------------------------


@p4x_router.get("/partner/search")
def search_partners(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
    q: str = "",
) -> list[PartnerSearchResult]:
    """Search transaction partners by name."""
    if len(q) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Suchbegriff muss mindestens 3 Zeichen lang sein.",
        )
    results = p4x_service.search_partners(db, q)
    return [PartnerSearchResult(**r) for r in results]


# ---------------------------------------------------------------------------
# Transaction partner assignment (Admin)
# ---------------------------------------------------------------------------


@p4x_router.post("/admin/transactions/{transaction_id}/set-partner")
def set_transaction_partner(
    transaction_id: int,
    data: SetPartnerRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> TransactionResponse:
    """Assign a partner to a transaction."""
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

    partner_dict = None
    if data.partner:
        partner_dict = {"type": data.partner.type, "id": data.partner.id}

    delegating_dict = None
    if data.delegatingPartner:
        delegating_dict = {
            "type": data.delegatingPartner.type,
            "id": data.delegatingPartner.id,
        }

    p4x_service.set_transaction_partner(
        db,
        tx,
        partner_dict,
        data.hasDelegatingPartner,
        delegating_dict,
    )
    db.refresh(tx)
    return _build_transaction_response(tx, db)


# ---------------------------------------------------------------------------
# Transaction edit (comment + attachment) (Admin)
# ---------------------------------------------------------------------------


@p4x_router.put("/admin/transactions/{transaction_id}")
async def update_transaction(
    transaction_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
    comment: Annotated[str | None, Form()] = None,
    delete_attachment: Annotated[bool, Form()] = False,  # noqa: FBT002
    file: UploadFile | None = None,
) -> TransactionResponse:
    """Update transaction comment or attachment."""
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

    file_bytes = None
    if file:
        if not file.content_type or "pdf" not in file.content_type:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Nur PDF-Dateien sind erlaubt.",
            )
        file_bytes = await file.read()
        if len(file_bytes) > 3 * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Datei darf maximal 3 MB groß sein.",
            )

    p4x_service.update_transaction_meta(
        db,
        tx,
        comment,
        file_bytes,
        delete_attachment,
    )
    db.refresh(tx)
    return _build_transaction_response(tx, db)


def _build_category_response(
    db: Session,
    cat: P4xCategory,
) -> CategoryWithUsageResponse:
    usage = p4x_service.get_category_usage(db, cat)
    return CategoryWithUsageResponse(
        id=cat.id,
        name=cat.name,
        label=cat.label,
        background_color=cat.background_color,
        text_color=cat.text_color,
        protected=cat.protected,
        used=usage,
    )


@p4x_router.get("/admin/categories")
def list_categories(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> list[CategoryWithUsageResponse]:
    """List all transaction categories."""
    cats = db.query(P4xCategory).all()
    return [_build_category_response(db, c) for c in cats]


@p4x_router.post("/admin/categories")
def create_category(
    data: CategorySaveRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> CategoryWithUsageResponse:
    """Create a new transaction category."""
    existing = (
        db.query(P4xCategory)
        .filter(
            P4xCategory.name == data.name,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Name muss eindeutig sein.",
        )

    now = datetime.now(UTC)
    cat = P4xCategory(
        name=data.name,
        label=data.label,
        background_color=data.background_color,
        text_color=data.text_color,
        created_at=now,
        updated_at=now,
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return _build_category_response(db, cat)


@p4x_router.put("/admin/categories/{category_id}")
def update_category(
    category_id: int,
    data: CategorySaveRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> CategoryWithUsageResponse:
    """Update a transaction category."""
    cat = db.query(P4xCategory).filter(P4xCategory.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden.")

    dup = (
        db.query(P4xCategory)
        .filter(
            P4xCategory.name == data.name,
            P4xCategory.id != cat.id,
        )
        .first()
    )
    if dup:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Name muss eindeutig sein.",
        )

    cat.name = data.name
    cat.label = data.label
    cat.background_color = data.background_color
    cat.text_color = data.text_color
    db.commit()
    db.refresh(cat)
    return _build_category_response(db, cat)


@p4x_router.delete("/admin/categories/{category_id}")
def delete_category_endpoint(
    category_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> dict[str, str]:
    """Delete a transaction category."""
    cat = db.query(P4xCategory).filter(P4xCategory.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden.")

    error = p4x_service.delete_category(db, cat)
    if error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error,
        )
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Category Filters (Admin)
# ---------------------------------------------------------------------------


def _build_filter_response(
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
        hitCount=p4x_service.get_filter_hit_count(db, f),
    )


def _get_filter_or_404(
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


@p4x_router.get("/admin/category-filters")
def list_category_filters(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> list[CategoryFilterResponse]:
    """List all category auto-assignment filters."""
    filters = db.query(P4xCategoryFilter).all()
    return [_build_filter_response(db, f) for f in filters]


@p4x_router.post("/admin/category-filters")
def create_category_filter(
    data: CategoryFilterSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> CategoryFilterResponse:
    """Create a new category filter rule."""
    if not (
        data.iban
        or data.min_amount is not None
        or data.max_amount is not None
        or data.subject
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Mindestens ein Filterkriterium muss gesetzt sein.",
        )

    if not db.query(P4xAccount).filter_by(id=data.p4x_account_id).first():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Konto existiert nicht.",
        )
    if not db.query(P4xCategory).filter_by(id=data.p4x_category_id).first():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Kategorie existiert nicht.",
        )

    existing = (
        db.query(P4xCategoryFilter)
        .filter(
            P4xCategoryFilter.name == data.name,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Name muss eindeutig sein.",
        )

    now = datetime.now(UTC)
    f = P4xCategoryFilter(
        name=data.name,
        p4x_account_id=data.p4x_account_id,
        iban=data.iban,
        min_amount=data.min_amount,
        max_amount=data.max_amount,
        subject=data.subject,
        subject_mode=data.subject_mode,
        p4x_category_id=data.p4x_category_id,
        created_at=now,
        updated_at=now,
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    p4x_service.apply_all_category_filters(db)
    return _build_filter_response(db, f)


@p4x_router.put("/admin/category-filters/{filter_id}")
def update_category_filter(
    filter_id: int,
    data: CategoryFilterSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> CategoryFilterResponse:
    """Update a category filter rule."""
    if not (
        data.iban
        or data.min_amount is not None
        or data.max_amount is not None
        or data.subject
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Mindestens ein Filterkriterium muss gesetzt sein.",
        )

    if not db.query(P4xAccount).filter_by(id=data.p4x_account_id).first():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Konto existiert nicht.",
        )
    if not db.query(P4xCategory).filter_by(id=data.p4x_category_id).first():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Kategorie existiert nicht.",
        )

    f = _get_filter_or_404(db, filter_id)

    dup = (
        db.query(P4xCategoryFilter)
        .filter(
            P4xCategoryFilter.name == data.name,
            P4xCategoryFilter.id != f.id,
        )
        .first()
    )
    if dup:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Name muss eindeutig sein.",
        )

    f.name = data.name
    f.p4x_account_id = data.p4x_account_id
    f.iban = data.iban
    f.min_amount = data.min_amount
    f.max_amount = data.max_amount
    f.subject = data.subject
    f.subject_mode = data.subject_mode
    f.p4x_category_id = data.p4x_category_id
    db.commit()
    db.refresh(f)
    p4x_service.apply_all_category_filters(db)
    return _build_filter_response(db, f)


@p4x_router.delete("/admin/category-filters/{filter_id}")
def delete_category_filter_endpoint(
    filter_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> dict[str, str]:
    """Delete a category filter rule."""
    f = _get_filter_or_404(db, filter_id)
    p4x_service.delete_category_filter(db, f)
    return {"status": "ok"}


@p4x_router.get("/admin/category-filters/{filter_id}/filter2direct")
def get_filter2direct_preview(
    filter_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> dict[
    str, int | CategoryFilterResponse | CategoryResponse | list[FilterHitResponse]
]:
    """Preview which transactions a filter would convert to direct assignments."""
    f = _get_filter_or_404(db, filter_id)
    _, partner_count = p4x_service.get_warnings_partner(db)
    _, category_count = p4x_service.get_warnings_category(db)
    hits = p4x_service.get_filter_hits(db, f)

    return {
        "warningsCount": partner_count + category_count,
        "filter": _build_filter_response(db, f),
        "category": CategoryResponse(
            id=f.category.id,
            name=f.category.name,
            label=f.category.label,
            background_color=f.category.background_color,
            text_color=f.category.text_color,
            protected=f.category.protected,
        ),
        "hits": [
            FilterHitResponse(
                booking=str(tx.booking) if tx.booking else None,
                amount=tx.amount,
                subject=tx.subject,
                iban=tx.iban,
            )
            for tx in hits
        ],
    }


@p4x_router.post("/admin/category-filters/{filter_id}/filter2direct")
def process_filter2direct(
    filter_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> dict[str, list[FilterHitResponse]]:
    """Convert all filter-matched transactions to direct category assignments."""
    f = _get_filter_or_404(db, filter_id)
    error = p4x_service.filter_to_direct(db, f)
    if error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error,
        )
    remaining_hits = p4x_service.get_filter_hits(db, f)
    return {
        "hits": [
            FilterHitResponse(
                booking=str(tx.booking) if tx.booking else None,
                amount=tx.amount,
                subject=tx.subject,
                iban=tx.iban,
            )
            for tx in remaining_hits
        ],
    }


# ---------------------------------------------------------------------------
# Transaction category direct (Admin)
# ---------------------------------------------------------------------------


@p4x_router.post("/admin/transactions/{transaction_id}/set-category-direct")
def set_category_direct_endpoint(
    transaction_id: int,
    data: list[dict[str, object]],
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> TransactionResponse:
    """Manually assign a category to a transaction."""
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

    error = p4x_service.set_category_direct(db, tx, data)
    if error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error,
        )
    db.refresh(tx)
    return _build_transaction_response(tx, db)


@p4x_router.delete("/admin/transactions/{transaction_id}/unset-category-direct")
def unset_category_direct_endpoint(
    transaction_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> TransactionResponse:
    """Remove a manual category assignment from a transaction."""
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

    p4x_service.unset_category_direct(db, tx)
    db.refresh(tx)
    return _build_transaction_response(tx, db)


# ---------------------------------------------------------------------------
# Transactions by filter (Admin)
# ---------------------------------------------------------------------------


@p4x_router.get(
    "/admin/accounts/{account_id}/transactions/by-filter/{filter_id}",
)
def get_transactions_by_filter(
    account_id: int,
    filter_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
    page: int = 1,
) -> PaginatedTransactions:
    """List transactions matched by a specific category filter (paginated)."""
    account = _get_account_or_404(db, account_id)
    items, total = p4x_service.get_transactions_by_filter(
        db,
        account,
        filter_id,
        page,
    )
    return PaginatedTransactions(
        items=[_build_transaction_response(tx, db) for tx in items],
        total=total,
        page=page,
        per_page=p4x_service.PAGINATION_SIZE,
    )


# ---------------------------------------------------------------------------
# Fee Config (Admin)
# ---------------------------------------------------------------------------


def _build_fee_response(fee: P4xFee) -> FeeResponse:
    start_date = fee.start
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date[:10])
    return FeeResponse(
        start=str(start_date.replace(day=1)),
        fee=fee.fee,
        protected=bool(fee.protected),
    )


@p4x_router.get("/admin/fee-config")
def list_fee_config(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> list[FeeResponse]:
    """List all membership fee configurations."""
    fees = p4x_service.get_all_fees(db)
    return [_build_fee_response(f) for f in fees]


@p4x_router.post("/admin/fee-config")
def create_fee(
    data: FeeCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> list[FeeResponse]:
    """Create a new fee configuration entry."""
    _, error = p4x_service.create_fee(db, data.year, data.month, data.fee)
    if error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error,
        )
    fees = p4x_service.get_all_fees(db)
    return [_build_fee_response(f) for f in fees]


@p4x_router.delete("/admin/fee-config/{start}")
def delete_fee(
    start: str,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> list[FeeResponse]:
    """Delete a fee configuration entry."""
    error = p4x_service.delete_fee(db, start)
    if error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error,
        )
    fees = p4x_service.get_all_fees(db)
    return [_build_fee_response(f) for f in fees]


# ---------------------------------------------------------------------------
# Fee Members
# ---------------------------------------------------------------------------


def _build_fee_member_response(
    db: Session,
    member: Member,
) -> FeeMemberResponse:
    balance_data = p4x_service.calculate_fee_balance(db, member)

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
        p4x_init_balance=member.p4x_init_balance or Decimal(0),
        p4x_freed=bool(member.p4x_freed),
        p4x_comment=member.p4x_comment,
        balance=balance,
    )


@p4x_router.get("/fee-members/search")
def search_fee_members(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
    q: str = "",
) -> dict[str, list[dict[str, str | int]]]:
    """Search members with their fee payment status."""
    if len(q) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Suchbegriff muss mindestens 3 Zeichen lang sein.",
        )
    return {"data": p4x_service.search_fee_members(db, q)}


@p4x_router.get("/fee-members/{member_id}")
def get_fee_member(
    member_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
) -> FeeMemberResponse:
    """Return detailed fee payment data for a specific member."""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Mitglied nicht gefunden.")
    if not p4x_service.is_fee_member(member):
        raise HTTPException(status_code=404, detail="Kein Beitragsmitglied.")
    return _build_fee_member_response(db, member)


@p4x_router.post("/admin/fee-members/{member_id}")
def update_fee_member(
    member_id: int,
    data: FeeMemberUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> FeeMemberResponse:
    """Update fee exemption or notes for a member."""
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Mitglied nicht gefunden.")

    p4x_service.update_fee_member(db, member, data.model_dump())
    db.refresh(member)
    return _build_fee_member_response(db, member)


@p4x_router.get("/fee-debtors")
def get_fee_debtors(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
) -> list[DebtorResponse]:
    """List all members with outstanding fee debts."""
    debtors = p4x_service.get_debtors(db)
    return [
        DebtorResponse(
            id=int(d["id"]),
            cn=str(d["cn"]),
            balance=Decimal(str(d["balance"])),
        )
        for d in debtors
    ]


# ---------------------------------------------------------------------------
# SumUp
# ---------------------------------------------------------------------------


@p4x_router.get("/sumup/balance")
def get_sumup_balance(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
) -> SumUpBalanceResponse:
    """Return the current SumUp terminal balance and recent transactions."""
    data = p4x_service.get_sumup_balance(db)
    return SumUpBalanceResponse(**data)


# ---------------------------------------------------------------------------
# Summary Report (Admin)
# ---------------------------------------------------------------------------


@p4x_router.post("/admin/summary")
def download_summary(
    data: SummaryOrderRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> Response:
    """Generate and download a PDF financial summary report."""
    if data.end < data.start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Enddatum muss nach dem Startdatum liegen.",
        )

    xlsx_bytes, pdf_attachments = p4x_service.generate_summary_xlsx(
        db,
        data.start,
        data.end,
    )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f"Abrechnung_{data.start}_{data.end}.xlsx",
            xlsx_bytes,
        )
        for name, pdf_bytes in pdf_attachments:
            zf.writestr(name, pdf_bytes)

    filename = f"Abrechnung_{data.start}_bis_{data.end}.zip"
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
