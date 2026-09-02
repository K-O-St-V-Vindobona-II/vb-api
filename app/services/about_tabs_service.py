from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from app.models.enums import AboutTabSlot
from app.models.public_site_about_tab import KNOWN_SLOTS, PublicSiteAboutTab

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_SLOT_ORDER = {slot: index for index, slot in enumerate(KNOWN_SLOTS)}


def list_tabs(db: Session) -> list[PublicSiteAboutTab]:
    """All 3 fixed tabs, in the fixed anfang/mkv/heute display order (there
    is no sort_order column - the order is the identity, see KNOWN_SLOTS)."""
    tabs = db.query(PublicSiteAboutTab).all()
    return sorted(tabs, key=lambda tab: _SLOT_ORDER[tab.slot])


def get_tabs_by_slot(db: Session) -> dict[AboutTabSlot, PublicSiteAboutTab]:
    """All 3 fixed tabs keyed by slot, for building the public combined
    site-content response."""
    return {tab.slot: tab for tab in db.query(PublicSiteAboutTab).all()}


def get_tab_or_404(db: Session, slot: str) -> PublicSiteAboutTab:
    # slot backs a native Postgres ENUM column - an unknown value must be
    # rejected here in Python before it ever reaches the query, since
    # Postgres raises a hard DataError (not just "no rows") when comparing
    # an ENUM column against a string that isn't one of its members.
    tab = (
        db.query(PublicSiteAboutTab).filter_by(slot=slot).first()
        if slot in AboutTabSlot
        else None
    )
    if not tab:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tab nicht gefunden.",
        )
    return tab


def update_tab(db: Session, tab: PublicSiteAboutTab, title: str, body: str) -> None:
    tab.title = title
    tab.body = body
    db.commit()
