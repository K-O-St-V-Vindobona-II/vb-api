from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.public_site_social_link import PublicSiteSocialLink
from app.services.reorder_service import find_reorder_neighbor


def list_admin_links(db: Session) -> list[PublicSiteSocialLink]:
    return (
        db.query(PublicSiteSocialLink).order_by(PublicSiteSocialLink.sort_order).all()
    )


def list_enabled_links(db: Session) -> list[PublicSiteSocialLink]:
    return (
        db.query(PublicSiteSocialLink)
        .filter(PublicSiteSocialLink.is_enabled.is_(True))
        .order_by(PublicSiteSocialLink.sort_order)
        .all()
    )


def get_link_or_404(db: Session, link_id: int) -> PublicSiteSocialLink:
    link = db.get(PublicSiteSocialLink, link_id)
    if not link:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Social-Media-Link nicht gefunden.",
        )
    return link


def create_link(
    db: Session,
    platform: str,
    label: str,
    url: str,
    is_enabled: bool,  # noqa: FBT001
) -> PublicSiteSocialLink:
    next_sort_order = (
        db.query(func.max(PublicSiteSocialLink.sort_order)).scalar() or 0
    ) + 1
    link = PublicSiteSocialLink(
        platform=platform,
        label=label,
        url=url,
        is_enabled=is_enabled,
        sort_order=next_sort_order,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def update_link(
    db: Session,
    link: PublicSiteSocialLink,
    label: str,
    url: str,
    is_enabled: bool,  # noqa: FBT001
) -> None:
    link.label = label
    link.url = url
    link.is_enabled = is_enabled
    db.commit()


def move_link(
    db: Session, link: PublicSiteSocialLink, direction: Literal["up", "down"]
) -> None:
    neighbor = find_reorder_neighbor(
        db,
        PublicSiteSocialLink,
        PublicSiteSocialLink.sort_order,
        link.sort_order,
        direction,
    )
    if not neighbor:
        return

    link.sort_order, neighbor.sort_order = neighbor.sort_order, link.sort_order
    db.commit()


def delete_link(db: Session, link: PublicSiteSocialLink) -> None:
    db.delete(link)
    db.commit()
