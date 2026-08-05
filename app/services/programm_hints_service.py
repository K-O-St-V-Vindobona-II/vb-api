from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.public_site_programm_hint import PublicSiteProgrammHint
from app.services.reorder_service import find_reorder_neighbor


def list_hints(db: Session) -> list[PublicSiteProgrammHint]:
    return (
        db.query(PublicSiteProgrammHint)
        .order_by(PublicSiteProgrammHint.sort_order)
        .all()
    )


def get_hint_or_404(db: Session, hint_id: int) -> PublicSiteProgrammHint:
    hint = db.get(PublicSiteProgrammHint, hint_id)
    if not hint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hinweis nicht gefunden.",
        )
    return hint


def create_hint(db: Session, text: str) -> PublicSiteProgrammHint:
    next_sort_order = (
        db.query(func.max(PublicSiteProgrammHint.sort_order)).scalar() or 0
    ) + 1
    hint = PublicSiteProgrammHint(content=text, sort_order=next_sort_order)
    db.add(hint)
    db.commit()
    db.refresh(hint)
    return hint


def update_hint(db: Session, hint: PublicSiteProgrammHint, text: str) -> None:
    hint.content = text
    db.commit()


def move_hint(
    db: Session, hint: PublicSiteProgrammHint, direction: Literal["up", "down"]
) -> None:
    neighbor = find_reorder_neighbor(
        db,
        PublicSiteProgrammHint,
        PublicSiteProgrammHint.sort_order,
        hint.sort_order,
        direction,
    )
    if not neighbor:
        return

    hint.sort_order, neighbor.sort_order = neighbor.sort_order, hint.sort_order
    db.commit()


def delete_hint(db: Session, hint: PublicSiteProgrammHint) -> None:
    db.delete(hint)
    db.commit()
