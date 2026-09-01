from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from sqlalchemy.orm.query import RowReturningQuery

    from app.schemas.p4x import CategoryFilterSaveRequest, CategorySaveRequest

from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
from app.models.p4x_transaction import P4xTransaction
from app.services import p4x_account_service

# ---------------------------------------------------------------------------
# Category filter engine
# ---------------------------------------------------------------------------


def apply_single_filter(db: Session, category_filter: P4xCategoryFilter) -> None:
    tx_with_directs = {
        r[0]
        for r in db.query(P4xCategoryDirect.p4x_transaction_id)
        .filter(
            P4xCategoryDirect.deleted_at.is_(None),
        )
        .distinct()
        .all()
    }
    _apply_filter_with_cache(db, category_filter, tx_with_directs)
    db.commit()


def _apply_category_filter_criteria(
    query: RowReturningQuery[tuple[int]],
    category_filter: P4xCategoryFilter,
    tx_with_directs: set[int],
) -> RowReturningQuery[tuple[int]]:
    if tx_with_directs:
        query = query.filter(~P4xTransaction.id.in_(tx_with_directs))

    if category_filter.p4x_account_id:
        query = query.filter(
            P4xTransaction.p4x_account_id == category_filter.p4x_account_id
        )

    if category_filter.iban and len(category_filter.iban):
        query = query.filter(P4xTransaction.iban == category_filter.iban)

    if category_filter.min_amount is not None:
        query = query.filter(P4xTransaction.amount >= category_filter.min_amount)

    if category_filter.max_amount is not None:
        query = query.filter(P4xTransaction.amount <= category_filter.max_amount)

    if category_filter.subject is not None:
        subject = category_filter.subject
        subject_pattern = {
            "equals": subject,
            "contains": f"%{subject}%",
            "starts": f"{subject}%",
        }.get(category_filter.subject_mode)
        if subject_pattern is not None:
            query = query.filter(P4xTransaction.subject.ilike(subject_pattern))

    return query


def _apply_filter_with_cache(
    db: Session,
    category_filter: P4xCategoryFilter,
    tx_with_directs: set[int],
) -> None:
    db.query(P4xCategoryFilterHit).filter(
        P4xCategoryFilterHit.p4x_category_filter_id == category_filter.id,
    ).delete()

    query = db.query(P4xTransaction.id).filter(
        P4xTransaction.deleted_at.is_(None),
    )
    query = _apply_category_filter_criteria(query, category_filter, tx_with_directs)

    now = datetime.now(UTC)
    for (tx_id,) in query.all():
        db.add(
            P4xCategoryFilterHit(
                p4x_transaction_id=tx_id,
                p4x_category_filter_id=category_filter.id,
                created_at=now,
                updated_at=now,
            )
        )


def apply_all_category_filters_core(
    db: Session,
    *,
    truncate_first: bool = False,
) -> None:
    """Same as apply_all_category_filters but without committing - callers
    own the transaction boundary (e.g. import_and_apply_filters)."""
    if truncate_first:
        db.query(P4xCategoryFilterHit).delete()

    tx_with_directs = {
        r[0]
        for r in db.query(P4xCategoryDirect.p4x_transaction_id)
        .filter(
            P4xCategoryDirect.deleted_at.is_(None),
        )
        .distinct()
        .all()
    }

    for f in db.query(P4xCategoryFilter).all():
        _apply_filter_with_cache(db, f, tx_with_directs)


def apply_all_category_filters(
    db: Session,
    *,
    truncate_first: bool = False,
) -> None:
    apply_all_category_filters_core(db, truncate_first=truncate_first)
    db.commit()


# ---------------------------------------------------------------------------
# Category CRUD
# ---------------------------------------------------------------------------


def get_all_categories(db: Session) -> list[P4xCategory]:
    return db.query(P4xCategory).all()


def get_category_usage(db: Session, category: P4xCategory) -> dict[str, int]:
    filter_count = (
        db.query(P4xCategoryFilter)
        .filter(
            P4xCategoryFilter.p4x_category_id == category.id_uuid,
        )
        .count()
    )
    direct_count = (
        db.query(P4xCategoryDirect)
        .filter(
            P4xCategoryDirect.p4x_category_id == category.id,
            P4xCategoryDirect.deleted_at.is_(None),
        )
        .count()
    )
    return {"filter": filter_count, "direct": direct_count}


def create_category(db: Session, data: CategorySaveRequest) -> P4xCategory:
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
    category = P4xCategory(
        name=data.name,
        label=data.label,
        background_color=data.background_color,
        text_color=data.text_color,
        created_at=now,
        updated_at=now,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def update_category(
    db: Session,
    category: P4xCategory,
    data: CategorySaveRequest,
) -> P4xCategory:
    dup = (
        db.query(P4xCategory)
        .filter(
            P4xCategory.name == data.name,
            P4xCategory.id != category.id,
        )
        .first()
    )
    if dup:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Name muss eindeutig sein.",
        )

    category.name = data.name
    category.label = data.label
    category.background_color = data.background_color
    category.text_color = data.text_color
    db.commit()
    db.refresh(category)
    return category


def delete_category(db: Session, category: P4xCategory) -> str | None:
    """Returns an error message string if deletion is blocked, else None."""
    if category.protected:
        return "Geschützte Kategorien können nicht gelöscht werden."
    usage = get_category_usage(db, category)
    if usage["filter"] > 0 or usage["direct"] > 0:
        return "Kategorie ist in Verwendung und kann nicht gelöscht werden."
    db.delete(category)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Category filter CRUD helpers
# ---------------------------------------------------------------------------


def get_all_category_filters(db: Session) -> list[P4xCategoryFilter]:
    return db.query(P4xCategoryFilter).all()


def get_filter_hit_count(db: Session, category_filter: P4xCategoryFilter) -> int:
    tx_with_directs = {
        r[0]
        for r in db.query(P4xCategoryDirect.p4x_transaction_id)
        .filter(
            P4xCategoryDirect.deleted_at.is_(None),
        )
        .distinct()
        .all()
    }

    query = db.query(P4xCategoryFilterHit).filter(
        P4xCategoryFilterHit.p4x_category_filter_id == category_filter.id,
    )
    if tx_with_directs:
        query = query.filter(
            ~P4xCategoryFilterHit.p4x_transaction_id.in_(tx_with_directs),
        )
    return query.count()


def get_filter_hits(
    db: Session,
    category_filter: P4xCategoryFilter,
) -> list[P4xTransaction]:
    tx_with_directs = {
        r[0]
        for r in db.query(P4xCategoryDirect.p4x_transaction_id)
        .filter(
            P4xCategoryDirect.deleted_at.is_(None),
        )
        .distinct()
        .all()
    }

    hit_tx_ids = [
        r[0]
        for r in db.query(P4xCategoryFilterHit.p4x_transaction_id)
        .filter(
            P4xCategoryFilterHit.p4x_category_filter_id == category_filter.id,
        )
        .all()
    ]

    valid_ids = [tid for tid in hit_tx_ids if tid not in tx_with_directs]
    if not valid_ids:
        return []

    return (
        db.query(P4xTransaction)
        .filter(
            P4xTransaction.id.in_(valid_ids),
            P4xTransaction.deleted_at.is_(None),
        )
        .all()
    )


def _validate_category_filter_input(
    db: Session,
    data: CategoryFilterSaveRequest,
) -> None:
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
    if not db.query(P4xAccount).filter_by(id_uuid=data.p4x_account_id).first():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Konto existiert nicht.",
        )
    if not db.query(P4xCategory).filter_by(id_uuid=data.p4x_category_id).first():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Kategorie existiert nicht.",
        )


def create_category_filter(
    db: Session,
    data: CategoryFilterSaveRequest,
) -> P4xCategoryFilter:
    _validate_category_filter_input(db, data)
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
    category_filter = P4xCategoryFilter(
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
    db.add(category_filter)
    db.flush()
    apply_all_category_filters_core(db)
    db.commit()
    db.refresh(category_filter)
    return category_filter


def update_category_filter(
    db: Session,
    category_filter: P4xCategoryFilter,
    data: CategoryFilterSaveRequest,
) -> P4xCategoryFilter:
    _validate_category_filter_input(db, data)
    dup = (
        db.query(P4xCategoryFilter)
        .filter(
            P4xCategoryFilter.name == data.name,
            P4xCategoryFilter.id != category_filter.id,
        )
        .first()
    )
    if dup:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Name muss eindeutig sein.",
        )

    category_filter.name = data.name
    category_filter.p4x_account_id = data.p4x_account_id
    category_filter.iban = data.iban
    category_filter.min_amount = data.min_amount
    category_filter.max_amount = data.max_amount
    category_filter.subject = data.subject
    category_filter.subject_mode = data.subject_mode
    category_filter.p4x_category_id = data.p4x_category_id
    db.flush()
    apply_all_category_filters_core(db)
    db.commit()
    db.refresh(category_filter)
    return category_filter


def delete_category_filter(db: Session, category_filter: P4xCategoryFilter) -> None:
    db.query(P4xCategoryFilterHit).filter(
        P4xCategoryFilterHit.p4x_category_filter_id == category_filter.id,
    ).delete()
    db.delete(category_filter)
    db.commit()


def filter_to_direct(db: Session, category_filter: P4xCategoryFilter) -> str | None:
    """Convert filter hits to direct assignments. Returns error message or None."""
    _, partner_count = p4x_account_service.get_warnings_partner(db)
    _, category_count = p4x_account_service.get_warnings_category(db)
    if partner_count + category_count > 0:
        return "Es gibt noch offene Warnungen. Bitte zuerst alle Warnungen beheben."

    hits = get_filter_hits(db, category_filter)
    for tx in hits:
        _set_category_direct_core(
            db,
            tx,
            [
                {
                    # p4x_category_directs.p4x_category_id is still the
                    # category's legacy integer id (that table's own
                    # UUID cutover is a later slice), while
                    # category_filter.p4x_category_id now stores
                    # id_uuid - bridged via the already-joined category
                    # relationship.
                    "p4x_category_id": category_filter.category.id,
                    "amount": tx.amount,
                },
            ],
        )

    db.query(P4xCategoryFilterHit).filter(
        P4xCategoryFilterHit.p4x_category_filter_id == category_filter.id,
    ).delete()
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Category direct assignments
# ---------------------------------------------------------------------------


def _set_category_direct_core(
    db: Session,
    transaction: P4xTransaction,
    slots: list[dict[str, object]],
) -> str | None:
    """Same as set_category_direct but without committing - callers own the
    transaction boundary (e.g. filter_to_direct's batch loop, which must
    not commit once per transaction)."""
    valid_slots = [s for s in slots if s.get("p4x_category_id") and s.get("amount")]

    if valid_slots:
        total = sum(Decimal(str(s["amount"])) for s in valid_slots)
        if transaction.amount != total:
            return "Summe der Beträge stimmt nicht mit dem Transaktionsbetrag überein."

    now = datetime.now(UTC)

    db.query(P4xCategoryDirect).filter(
        P4xCategoryDirect.p4x_transaction_id == transaction.id,
        P4xCategoryDirect.deleted_at.is_(None),
    ).update({"deleted_at": now})

    for slot in valid_slots:
        db.add(
            P4xCategoryDirect(
                p4x_transaction_id=transaction.id,
                p4x_category_id=slot["p4x_category_id"],
                amount=Decimal(str(slot["amount"])),
            )
        )

    return None


def set_category_direct(
    db: Session,
    transaction: P4xTransaction,
    slots: list[dict[str, object]],
) -> str | None:
    """Set direct category assignments. Returns error message or None."""
    error = _set_category_direct_core(db, transaction, slots)
    if error:
        return error
    db.commit()
    return None


def unset_category_direct(db: Session, transaction: P4xTransaction) -> None:
    now = datetime.now(UTC)
    db.query(P4xCategoryDirect).filter(
        P4xCategoryDirect.p4x_transaction_id == transaction.id,
        P4xCategoryDirect.deleted_at.is_(None),
    ).update({"deleted_at": now})
    apply_all_category_filters_core(db)
    db.commit()
