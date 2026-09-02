import base64
import io
import json
import re
import uuid
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.api.auth_guards import require_permission
from app.api.deps import get_current_user
from app.core.datetime_utils import local_today
from app.db.database import get_db
from app.models.member import Member
from app.models.p4x_fee import P4xFee
from app.schemas.p4x import (
    AccountResponse,
    AccountSaveRequest,
    CategoryFilterResponse,
    CategoryFilterSaveRequest,
    CategoryResponse,
    CategorySaveRequest,
    CategoryWithUsageResponse,
    DashboardResponse,
    FeeBalanceCount,
    FeeBalanceListItem,
    FeeBalanceResponse,
    FeeBalanceSum,
    FeeCreateRequest,
    FeeMemberResponse,
    FeeMemberSearchResponse,
    FeeMemberSelfResponse,
    FeeMemberUpdateRequest,
    FeeProgressEntry,
    FeeResponse,
    Filter2DirectPreviewResponse,
    Filter2DirectResultResponse,
    FilterHitResponse,
    ImportGiven,
    ImportResult,
    PaginatedTransactions,
    PartnerSearchResult,
    SetPartnerRequest,
    SummaryOrderRequest,
    SumUpBalanceResponse,
    TransactionRawResponse,
    TransactionResponse,
    TransactionsByMonthResponse,
    WarningsResponse,
)
from app.services import (
    p4x_account_service,
    p4x_category_service,
    p4x_fee_balance_service,
    p4x_import_service,
    p4x_partner_service,
    p4x_response_builders,
    p4x_summary_service,
)

p4x_router = APIRouter()

PREVIEW_LIMIT = 10


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@p4x_router.get("/accounts")
def get_dashboard(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
) -> DashboardResponse:
    """Return all bank accounts with balances, plus a capped preview (PREVIEW_LIMIT
    transactions each) of the two warning categories - see /warnings/partner and
    /warnings/category for the full paginated lists. Requires p4xView."""
    accounts = p4x_account_service.get_active_accounts(db)

    partner_items, partner_count = p4x_account_service.get_warnings_partner(
        db,
        limit=PREVIEW_LIMIT,
    )
    category_items, category_count = p4x_account_service.get_warnings_category(
        db,
        limit=PREVIEW_LIMIT,
    )

    categories = p4x_category_service.get_all_categories(db)

    return DashboardResponse(
        accounts=[
            p4x_response_builders.build_account_response(db, a) for a in accounts
        ],
        warnings_partner=WarningsResponse(
            count=partner_count,
            preview=[
                p4x_response_builders.build_transaction_response(tx, db)
                for tx in partner_items
            ],
        ),
        warnings_category=WarningsResponse(
            count=category_count,
            preview=[
                p4x_response_builders.build_transaction_response(tx, db)
                for tx in category_items
            ],
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
    """List transactions missing a partner assignment, paginated. Requires p4xView."""
    items, total = p4x_account_service.get_warnings_partner(db)
    start = (page - 1) * p4x_account_service.PAGINATION_SIZE
    end = start + p4x_account_service.PAGINATION_SIZE
    page_items = items[start:end]
    return PaginatedTransactions(
        items=[
            p4x_response_builders.build_transaction_response(tx, db)
            for tx in page_items
        ],
        total=total,
        page=page,
        per_page=p4x_account_service.PAGINATION_SIZE,
    )


@p4x_router.get("/warnings/category")
def get_warnings_category_list(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
    page: int = 1,
) -> PaginatedTransactions:
    """List transactions missing a category assignment, paginated. Requires p4xView."""
    items, total = p4x_account_service.get_warnings_category(db)
    start = (page - 1) * p4x_account_service.PAGINATION_SIZE
    end = start + p4x_account_service.PAGINATION_SIZE
    page_items = items[start:end]
    return PaginatedTransactions(
        items=[
            p4x_response_builders.build_transaction_response(tx, db)
            for tx in page_items
        ],
        total=total,
        page=page,
        per_page=p4x_account_service.PAGINATION_SIZE,
    )


# ---------------------------------------------------------------------------
# Account CRUD
# ---------------------------------------------------------------------------


@p4x_router.post("/admin/accounts", status_code=status.HTTP_201_CREATED)
def create_account(
    data: AccountSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> AccountResponse:
    """Create a new bank account. Requires p4xAdmin."""
    account = p4x_account_service.create_account(db, data)
    return p4x_response_builders.build_account_response(db, account)


@p4x_router.put("/admin/accounts/{account_id}")
def update_account(
    account_id: uuid.UUID,
    data: AccountSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> AccountResponse:
    """Update a bank account's details. Requires p4xAdmin."""
    account = p4x_response_builders.get_account_or_404(db, account_id)
    account = p4x_account_service.update_account(db, account, data)
    return p4x_response_builders.build_account_response(db, account)


@p4x_router.delete(
    "/admin/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_account(
    account_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> None:
    """Delete a bank account. Requires p4xAdmin."""
    account = p4x_response_builders.get_account_or_404(db, account_id)
    p4x_account_service.delete_account(db, account)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@p4x_router.post("/admin/accounts/{account_id}/import")
async def import_transactions(
    account_id: uuid.UUID,
    file: UploadFile,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> ImportResult:
    """Import bank transactions from a George-Bank JSON export (despite the CSV-sounding
    endpoint name - see p4x_import_service.parse_george_json). The uploaded filename
    must contain the account's IBAN as a sanity check, max 3 MB. Already existing
    category filters are applied automatically on import. Requires p4xAdmin."""
    account = p4x_response_builders.get_account_or_404(db, account_id)

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
    parse_result = p4x_import_service.parse_george_json(account.bic or "", raw_json)

    if not parse_result.success:
        return ImportResult(
            given=ImportGiven(p4x_account_id=account.id, parsed=False),
            message=parse_result.message,
        )

    original_structs = json.loads(raw_json)
    summary = p4x_import_service.import_and_apply_filters(
        db,
        account,
        parse_result.entries,
        original_structs,
    )

    db.refresh(account)
    account_data = p4x_response_builders.build_account_response(db, account)

    return ImportResult(
        given=ImportGiven(p4x_account_id=account.id, parsed=True),
        summary=summary,
        account=account_data,
    )


# ---------------------------------------------------------------------------
# Transactions by month / partner / category
# ---------------------------------------------------------------------------


@p4x_router.get(
    "/accounts/{account_id}/transactions/by-month/{year}/{month}",
)
def get_transactions_by_month(
    account_id: uuid.UUID,
    year: int,
    month: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
    page: int = 1,
) -> TransactionsByMonthResponse:
    """List transactions for a specific month, paginated, with the account's start/end
    balance for that month attached. Requires p4xView."""
    account = p4x_response_builders.get_account_or_404(db, account_id)
    items, total = p4x_account_service.get_transactions_by_month(
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

    return TransactionsByMonthResponse(
        items=[
            p4x_response_builders.build_transaction_response(tx, db) for tx in items
        ],
        total=total,
        page=page,
        per_page=p4x_account_service.PAGINATION_SIZE,
        startbalance=p4x_account_service.get_account_balance(db, account, last_of_prev),
        endbalance=p4x_account_service.get_account_balance(db, account, last_of_month),
    )


@p4x_router.get(
    "/accounts/{account_id}/transactions/by-partner/{partner_type}/{partner_id}",
)
def get_transactions_by_partner(
    account_id: uuid.UUID,
    partner_type: str,
    partner_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
    page: int = 1,
) -> PaginatedTransactions:
    """List transactions assigned to a specific partner (member or contact, see
    partner_type), paginated. Requires p4xView."""
    account = p4x_response_builders.get_account_or_404(db, account_id)
    items, total = p4x_account_service.get_transactions_by_partner(
        db,
        account,
        partner_type,
        partner_id,
        page,
    )
    return PaginatedTransactions(
        items=[
            p4x_response_builders.build_transaction_response(tx, db) for tx in items
        ],
        total=total,
        page=page,
        per_page=p4x_account_service.PAGINATION_SIZE,
    )


@p4x_router.get(
    "/accounts/{account_id}/transactions/by-category/{category_id}",
)
def get_transactions_by_category(
    account_id: uuid.UUID,
    category_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
    page: int = 1,
) -> PaginatedTransactions:
    """List transactions assigned to a specific category, paginated.
    Requires p4xView."""
    account = p4x_response_builders.get_account_or_404(db, account_id)
    items, total = p4x_account_service.get_transactions_by_category(
        db,
        account,
        category_id,
        page,
    )
    return PaginatedTransactions(
        items=[
            p4x_response_builders.build_transaction_response(tx, db) for tx in items
        ],
        total=total,
        page=page,
        per_page=p4x_account_service.PAGINATION_SIZE,
    )


# ---------------------------------------------------------------------------
# Transaction raw data and attachment
# ---------------------------------------------------------------------------


@p4x_router.get(
    "/accounts/{account_id}/transactions/raw/{transaction_id}",
)
def get_transaction_raw(
    account_id: uuid.UUID,
    transaction_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
) -> TransactionRawResponse:
    """Return the original, unprocessed row for a transaction exactly as imported from
    the bank export. Requires p4xView."""
    tx = p4x_response_builders.get_transaction_for_account(
        db, account_id, transaction_id
    )
    return TransactionRawResponse(raw=tx.raw)


@p4x_router.get(
    "/accounts/{account_id}/transactions/attachment/{transaction_id}",
)
def get_transaction_attachment(
    account_id: uuid.UUID,
    transaction_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
) -> StreamingResponse:
    """Download a transaction's PDF attachment (e.g. a scanned receipt), if one was
    uploaded. 404 if none exists. Requires p4xView."""
    tx = p4x_response_builders.get_transaction_for_account(
        db, account_id, transaction_id
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
    """Search transaction partners (members and contacts) by name, minimum 3 characters.
    Requires p4xView."""
    if len(q) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Suchbegriff muss mindestens 3 Zeichen lang sein.",
        )
    return p4x_partner_service.search_partners(db, q)


# ---------------------------------------------------------------------------
# Transaction partner assignment (Admin)
# ---------------------------------------------------------------------------


@p4x_router.post("/admin/transactions/{transaction_id}/set-partner")
def set_transaction_partner(
    transaction_id: uuid.UUID,
    data: SetPartnerRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> TransactionResponse:
    """Assign a partner to a transaction, optionally with a delegating partner for
    payments made on someone else's behalf. Requires p4xAdmin."""
    tx = p4x_response_builders.get_transaction_or_404(db, transaction_id)

    partner_dict = None
    if data.partner:
        partner_dict = {"type": data.partner.type, "id": data.partner.id}

    delegating_dict = None
    if data.delegatingPartner:
        delegating_dict = {
            "type": data.delegatingPartner.type,
            "id": data.delegatingPartner.id,
        }

    p4x_partner_service.set_transaction_partner(
        db,
        tx,
        partner_dict,
        data.hasDelegatingPartner,
        delegating_dict,
    )
    db.refresh(tx)
    return p4x_response_builders.build_transaction_response(tx, db)


# ---------------------------------------------------------------------------
# Transaction edit (comment + attachment) (Admin)
# ---------------------------------------------------------------------------


@p4x_router.put("/admin/transactions/{transaction_id}")
async def update_transaction(
    transaction_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
    comment: Annotated[str | None, Form()] = None,
    delete_attachment: Annotated[bool, Form()] = False,  # noqa: FBT002
    file: UploadFile | None = None,
) -> TransactionResponse:
    """Update a transaction's comment and/or PDF attachment. Multipart form data, not
    JSON - file must be a PDF, max 3 MB; delete_attachment removes an existing
    attachment without uploading a replacement. Requires p4xAdmin."""
    tx = p4x_response_builders.get_transaction_or_404(db, transaction_id)

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

    p4x_partner_service.update_transaction_meta(
        db,
        tx,
        comment,
        file_bytes,
        delete_attachment,
    )
    db.refresh(tx)
    return p4x_response_builders.build_transaction_response(tx, db)


@p4x_router.get("/admin/categories")
def list_categories(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> list[CategoryWithUsageResponse]:
    """List all transaction categories with their current usage counts.
    Requires p4xAdmin."""
    cats = p4x_category_service.get_all_categories(db)
    return [p4x_response_builders.build_category_response(db, c) for c in cats]


@p4x_router.post("/admin/categories", status_code=status.HTTP_201_CREATED)
def create_category(
    data: CategorySaveRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> CategoryWithUsageResponse:
    """Create a new transaction category. Requires p4xAdmin."""
    cat = p4x_category_service.create_category(db, data)
    return p4x_response_builders.build_category_response(db, cat)


@p4x_router.put("/admin/categories/{category_id}")
def update_category(
    category_id: uuid.UUID,
    data: CategorySaveRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> CategoryWithUsageResponse:
    """Update a transaction category's name, label, or colors. Requires p4xAdmin."""
    cat = p4x_response_builders.get_category_or_404(db, category_id)
    cat = p4x_category_service.update_category(db, cat, data)
    return p4x_response_builders.build_category_response(db, cat)


@p4x_router.delete(
    "/admin/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_category_endpoint(
    category_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> None:
    """Delete a transaction category. Fails with 409 if the category is protected or
    still in use - see p4x_category_service.delete_category. Requires p4xAdmin."""
    cat = p4x_response_builders.get_category_or_404(db, category_id)

    error = p4x_category_service.delete_category(db, cat)
    if error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error,
        )


# ---------------------------------------------------------------------------
# Category Filters (Admin)
# ---------------------------------------------------------------------------


@p4x_router.get("/admin/category-filters")
def list_category_filters(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> list[CategoryFilterResponse]:
    """List all category auto-assignment filter rules (IBAN/amount/subject-based).
    Requires p4xAdmin."""
    filters = p4x_category_service.get_all_category_filters(db)
    return [p4x_response_builders.build_filter_response(db, f) for f in filters]


@p4x_router.post("/admin/category-filters", status_code=status.HTTP_201_CREATED)
def create_category_filter(
    data: CategoryFilterSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> CategoryFilterResponse:
    """Create a new category auto-assignment filter rule. Requires p4xAdmin."""
    f = p4x_category_service.create_category_filter(db, data)
    return p4x_response_builders.build_filter_response(db, f)


@p4x_router.put("/admin/category-filters/{filter_id}")
def update_category_filter(
    filter_id: uuid.UUID,
    data: CategoryFilterSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> CategoryFilterResponse:
    """Update a category filter rule. Requires p4xAdmin."""
    f = p4x_response_builders.get_filter_or_404(db, filter_id)
    f = p4x_category_service.update_category_filter(db, f, data)
    return p4x_response_builders.build_filter_response(db, f)


@p4x_router.delete(
    "/admin/category-filters/{filter_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_category_filter_endpoint(
    filter_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> None:
    """Delete a category filter rule. Requires p4xAdmin."""
    f = p4x_response_builders.get_filter_or_404(db, filter_id)
    p4x_category_service.delete_category_filter(db, f)


@p4x_router.get("/admin/category-filters/{filter_id}/filter2direct")
def get_filter2direct_preview(
    filter_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> Filter2DirectPreviewResponse:
    """Preview which currently-unmatched transactions a filter rule would convert to
    permanent direct category assignments, without applying anything yet.
    Requires p4xAdmin."""
    f = p4x_response_builders.get_filter_or_404(db, filter_id)
    _, partner_count = p4x_account_service.get_warnings_partner(db)
    _, category_count = p4x_account_service.get_warnings_category(db)
    hits = p4x_category_service.get_filter_hits(db, f)

    return Filter2DirectPreviewResponse(
        warningsCount=partner_count + category_count,
        filter=p4x_response_builders.build_filter_response(db, f),
        category=CategoryResponse(
            id=f.category.id,
            name=f.category.name,
            label=f.category.label,
            background_color=f.category.background_color,
            text_color=f.category.text_color,
            protected=f.category.protected,
        ),
        hits=[
            FilterHitResponse(
                booking=str(tx.booking) if tx.booking else None,
                amount=tx.amount,
                subject=tx.subject,
                iban=tx.iban,
            )
            for tx in hits
        ],
    )


@p4x_router.post("/admin/category-filters/{filter_id}/filter2direct")
def process_filter2direct(
    filter_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> Filter2DirectResultResponse:
    """Apply a filter rule: convert every transaction it currently matches into a
    permanent direct category assignment - a one-time snapshot, not a live rule going
    forward. Requires p4xAdmin."""
    f = p4x_response_builders.get_filter_or_404(db, filter_id)
    error = p4x_category_service.filter_to_direct(db, f)
    if error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error,
        )
    remaining_hits = p4x_category_service.get_filter_hits(db, f)
    return Filter2DirectResultResponse(
        hits=[
            FilterHitResponse(
                booking=str(tx.booking) if tx.booking else None,
                amount=tx.amount,
                subject=tx.subject,
                iban=tx.iban,
            )
            for tx in remaining_hits
        ],
    )


# ---------------------------------------------------------------------------
# Transaction category direct (Admin)
# ---------------------------------------------------------------------------


@p4x_router.post("/admin/transactions/{transaction_id}/set-category-direct")
def set_category_direct_endpoint(
    transaction_id: uuid.UUID,
    data: list[dict[str, object]],
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> TransactionResponse:
    """Manually assign one or more categories to a transaction, overriding filter-based
    assignment. Requires p4xAdmin."""
    tx = p4x_response_builders.get_transaction_or_404(db, transaction_id)

    error = p4x_category_service.set_category_direct(db, tx, data)
    if error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error,
        )
    db.refresh(tx)
    return p4x_response_builders.build_transaction_response(tx, db)


@p4x_router.delete("/admin/transactions/{transaction_id}/unset-category-direct")
def unset_category_direct_endpoint(
    transaction_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> TransactionResponse:
    """Remove a transaction's manual category assignment, falling back to filter-based
    assignment if any filter still matches. Requires p4xAdmin."""
    tx = p4x_response_builders.get_transaction_or_404(db, transaction_id)

    p4x_category_service.unset_category_direct(db, tx)
    db.refresh(tx)
    return p4x_response_builders.build_transaction_response(tx, db)


# ---------------------------------------------------------------------------
# Transactions by filter (Admin)
# ---------------------------------------------------------------------------


@p4x_router.get(
    "/admin/accounts/{account_id}/transactions/by-filter/{filter_id}",
)
def get_transactions_by_filter(
    account_id: uuid.UUID,
    filter_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
    page: int = 1,
) -> PaginatedTransactions:
    """List transactions currently matched by a specific category filter rule,
    paginated. Requires p4xAdmin."""
    account = p4x_response_builders.get_account_or_404(db, account_id)
    items, total = p4x_account_service.get_transactions_by_filter(
        db,
        account,
        filter_id,
        page,
    )
    return PaginatedTransactions(
        items=[
            p4x_response_builders.build_transaction_response(tx, db) for tx in items
        ],
        total=total,
        page=page,
        per_page=p4x_account_service.PAGINATION_SIZE,
    )


# ---------------------------------------------------------------------------
# Fee Config (Admin)
# ---------------------------------------------------------------------------


def _build_fee_response(fee: P4xFee) -> FeeResponse:
    return FeeResponse(
        start=str(fee.start.replace(day=1)),
        fee=fee.fee,
        protected=bool(fee.protected),
    )


@p4x_router.get("/admin/fee-config")
def list_fee_config(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> list[FeeResponse]:
    """List all membership fee amounts by their effective start month.
    Requires p4xAdmin."""
    fees = p4x_fee_balance_service.get_all_fees(db)
    return [_build_fee_response(f) for f in fees]


@p4x_router.post("/admin/fee-config", status_code=status.HTTP_201_CREATED)
def create_fee(
    data: FeeCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> list[FeeResponse]:
    """Create a new fee amount effective from a given month. Requires p4xAdmin."""
    _, error = p4x_fee_balance_service.create_fee(db, data.year, data.month, data.fee)
    if error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error,
        )
    fees = p4x_fee_balance_service.get_all_fees(db)
    return [_build_fee_response(f) for f in fees]


@p4x_router.delete("/admin/fee-config/{start}")
def delete_fee(
    start: str,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> list[FeeResponse]:
    """Delete a fee configuration entry by its start month. Requires p4xAdmin."""
    error = p4x_fee_balance_service.delete_fee(db, start)
    if error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error,
        )
    fees = p4x_fee_balance_service.get_all_fees(db)
    return [_build_fee_response(f) for f in fees]


# ---------------------------------------------------------------------------
# Fee Members
# ---------------------------------------------------------------------------


def _build_fee_member_response(
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


@p4x_router.get("/fee-members/search")
def search_fee_members(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
    q: str = "",
) -> FeeMemberSearchResponse:
    """Search fee-liable members by name, with each result's current payment status.
    Minimum 3 characters. Requires p4xView."""
    if len(q) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Suchbegriff muss mindestens 3 Zeichen lang sein.",
        )
    return FeeMemberSearchResponse.model_validate(
        {"data": p4x_fee_balance_service.search_fee_members(db, q)}
    )


# NOTE: these two "me" routes must stay registered before
# "/fee-members/{member_id}" below. member_id has no explicit path
# converter, so Starlette compiles a generic single-segment pattern for it;
# if {member_id} were registered first, "/fee-members/me" would match THAT
# route and fail uuid-conversion with a 422 instead of falling through to
# these routes. Same technique already used by /fee-members/search above.
@p4x_router.get("/fee-members/me")
def get_own_fee_member(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> FeeMemberSelfResponse:
    """Return the authenticated member's own fee account (self-service).

    No admin permission required - every member may see their own account.
    p4x_comment (an admin-internal note) is deliberately omitted from the
    response shape, see FeeMemberSelfResponse.
    """
    if not p4x_fee_balance_service.is_fee_member(current_user):
        raise HTTPException(status_code=404, detail="Kein Beitragsmitglied.")
    full = _build_fee_member_response(db, current_user)
    return FeeMemberSelfResponse.model_validate(full, from_attributes=True)


@p4x_router.get("/fee-members/me/export")
def export_own_fee_member(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> Response:
    """Export the authenticated member's own fee account statement as an XLSX file. Same
    self-service scope as GET /fee-members/me - 404 if not a fee member or no account
    data exists yet."""
    if not p4x_fee_balance_service.is_fee_member(current_user):
        raise HTTPException(status_code=404, detail="Kein Beitragsmitglied.")
    full = _build_fee_member_response(db, current_user)
    if full.balance is None:
        raise HTTPException(status_code=404, detail="Keine Kontodaten vorhanden.")
    xlsx_bytes = p4x_summary_service.generate_fee_member_xlsx(full)
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", current_user.cn)
    filename = f"Beitragskonto_{safe_name}_{local_today()}.xlsx"
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@p4x_router.get("/fee-members/{member_id}")
def get_fee_member(
    member_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
) -> FeeMemberResponse:
    """Return detailed fee payment data (balance, payment history) for a specific
    member. 404 if the member isn't fee-liable. Requires p4xView."""
    member = p4x_response_builders.get_member_or_404(db, member_id)
    if not p4x_fee_balance_service.is_fee_member(member):
        raise HTTPException(status_code=404, detail="Kein Beitragsmitglied.")
    return _build_fee_member_response(db, member)


@p4x_router.get("/fee-members/{member_id}/export")
def export_fee_member(
    member_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
) -> Response:
    """Export a specific member's fee account statement as an XLSX
    file. Requires p4xView."""
    member = p4x_response_builders.get_member_or_404(db, member_id)
    response = _build_fee_member_response(db, member)
    if response.balance is None:
        raise HTTPException(status_code=404, detail="Keine Kontodaten vorhanden.")
    xlsx_bytes = p4x_summary_service.generate_fee_member_xlsx(response)

    safe_name = re.sub(r'[\\/:*?"<>|]', "_", member.cn)
    filename = f"Beitragskonto_{safe_name}_{local_today()}.xlsx"
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@p4x_router.post("/admin/fee-members/{member_id}")
def update_fee_member(
    member_id: uuid.UUID,
    data: FeeMemberUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> FeeMemberResponse:
    """Update a member's fee exemption status, initial balance/date, or admin-internal
    comment. Requires p4xAdmin."""
    member = p4x_response_builders.get_member_or_404(db, member_id)

    p4x_fee_balance_service.update_fee_member(db, member, data.model_dump())
    db.refresh(member)
    return _build_fee_member_response(db, member)


@p4x_router.get("/fee-balances")
def get_fee_balances(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
) -> list[FeeBalanceListItem]:
    """List the current fee balance for every fee-liable member (not just debtors).
    Requires p4xView."""
    balances = p4x_fee_balance_service.get_fee_balances(db)
    return [
        FeeBalanceListItem(
            id=b["id"],
            cn=b["cn"],
            p4x_freed=b["p4x_freed"],
            balance=b["end_balance"],
        )
        for b in balances
    ]


# ---------------------------------------------------------------------------
# SumUp
# ---------------------------------------------------------------------------


@p4x_router.get("/sumup/balance")
def get_sumup_balance(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xView"))],
) -> SumUpBalanceResponse:
    """Return the SumUp card-terminal's current balance and its most recent
    transactions. Requires p4xView."""
    return p4x_summary_service.get_sumup_balance(db)


# ---------------------------------------------------------------------------
# Summary Report (Admin)
# ---------------------------------------------------------------------------


@p4x_router.post("/admin/summary")
def download_summary(
    data: SummaryOrderRequest,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("p4xAdmin"))],
) -> Response:
    """Generate a financial summary report for a date range: an XLSX workbook plus every
    referenced transaction's PDF attachment, bundled as a ZIP. Requires p4xAdmin."""
    if data.end < data.start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Enddatum muss nach dem Startdatum liegen.",
        )

    xlsx_bytes, pdf_attachments = p4x_summary_service.generate_summary_xlsx(
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
