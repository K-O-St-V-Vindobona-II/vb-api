from typing import TYPE_CHECKING, Literal

from fastapi import HTTPException, status
from sqlalchemy import func

from app.models.public_site_quote import PublicSiteQuote
from app.services.reorder_service import find_reorder_neighbor

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def list_quotes(db: Session) -> list[PublicSiteQuote]:
    return db.query(PublicSiteQuote).order_by(PublicSiteQuote.sort_order).all()


def get_quote_or_404(db: Session, quote_id: int) -> PublicSiteQuote:
    quote = db.get(PublicSiteQuote, quote_id)
    if not quote:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zitat nicht gefunden.",
        )
    return quote


def create_quote(db: Session, quote: str, author: str) -> PublicSiteQuote:
    next_sort_order = (db.query(func.max(PublicSiteQuote.sort_order)).scalar() or 0) + 1
    row = PublicSiteQuote(quote=quote, author=author, sort_order=next_sort_order)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_quote(db: Session, row: PublicSiteQuote, quote: str, author: str) -> None:
    row.quote = quote
    row.author = author
    db.commit()


def move_quote(
    db: Session, row: PublicSiteQuote, direction: Literal["up", "down"]
) -> None:
    neighbor = find_reorder_neighbor(
        db, PublicSiteQuote, PublicSiteQuote.sort_order, row.sort_order, direction
    )
    if not neighbor:
        return

    row.sort_order, neighbor.sort_order = neighbor.sort_order, row.sort_order
    db.commit()


def delete_quote(db: Session, row: PublicSiteQuote) -> None:
    db.delete(row)
    db.commit()
