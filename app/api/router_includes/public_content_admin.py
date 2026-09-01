import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.auth_guards import require_permission
from app.db.database import get_db
from app.models.member import Member
from app.schemas.base import MoveRequest, StatusResponse
from app.schemas.public_content import (
    AboutTabAdminResponse,
    AboutTabUpdateRequest,
    ProgrammHintRequest,
    ProgrammHintResponse,
    QuoteRequest,
    QuoteResponse,
    SiteSettingsResponse,
    SiteSettingsUpdateRequest,
    SocialLinkAdminResponse,
    SocialLinkCreateRequest,
    SocialLinkUpdateRequest,
)
from app.services import (
    about_tabs_service,
    programm_hints_service,
    quotes_service,
    site_settings_service,
    social_links_service,
)

# Every route below requires the "publicContentEditor" permission - this
# router manages content for the public site, not the public site itself
# (see public_site.py for the unauthenticated counterpart). Sibling of
# public_gallery_admin.py, which covers the gallery specifically.
public_content_admin_router = APIRouter()

RequirePublicContentEditor = Annotated[
    Member, Depends(require_permission("publicContentEditor"))
]

AboutTabSlot = Literal["anfang", "mkv", "heute"]


# --- About tabs -----------------------------------------------------------


@public_content_admin_router.get("/about-tabs")
def list_about_tabs(
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> list[AboutTabAdminResponse]:
    """List the 3 fixed "Über uns" tabs (anfang/mkv/heute)."""
    return [
        AboutTabAdminResponse.model_validate(tab)
        for tab in about_tabs_service.list_tabs(db)
    ]


@public_content_admin_router.put("/about-tabs/{slot}")
def update_about_tab(
    slot: AboutTabSlot,
    data: AboutTabUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> AboutTabAdminResponse:
    """Update one fixed "Über uns" tab's title and body by slot. Links in
    the body must use the `[text](url)` mini-syntax (see
    AboutTabUpdateRequest); slot itself cannot be changed - the 3 tabs are
    a fixed set (see PublicSiteAboutTab.KNOWN_SLOTS)."""
    tab = about_tabs_service.get_tab_or_404(db, slot)
    about_tabs_service.update_tab(db, tab, data.title, data.body)
    return AboutTabAdminResponse.model_validate(tab)


# --- Site settings (video + calendar) --------------------------------------


@public_content_admin_router.get("/settings")
def get_settings(
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> SiteSettingsResponse:
    """Return the singleton site-wide settings row: the "Über uns" video
    heading + YouTube id, the Programm section's Google Calendar id, and
    the gallery heading."""
    return SiteSettingsResponse.model_validate(site_settings_service.get_settings(db))


@public_content_admin_router.put("/settings")
def update_settings(
    data: SiteSettingsUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> SiteSettingsResponse:
    """Update the singleton site-wide settings. youtube_url/calendar_id
    accept a full pasted link (video URL, share link, or calendar embed
    URL) and are normalized to the bare video id / calendar id before
    saving - see SiteSettingsUpdateRequest's validators."""
    settings = site_settings_service.get_settings(db)
    site_settings_service.update_settings(
        db,
        settings,
        heading=data.about_video_heading,
        youtube_id=data.youtube_url,
        calendar_id=data.calendar_id,
        gallery_heading=data.gallery_heading,
    )
    return SiteSettingsResponse.model_validate(settings)


# --- Programm hints ---------------------------------------------------------


@public_content_admin_router.get("/programm-hints")
def list_programm_hints(
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> list[ProgrammHintResponse]:
    """List the "Hinweise" bullets shown under the public Programm
    section, in display order."""
    return [
        ProgrammHintResponse.model_validate(hint)
        for hint in programm_hints_service.list_hints(db)
    ]


@public_content_admin_router.post(
    "/programm-hints", status_code=status.HTTP_201_CREATED
)
def create_programm_hint(
    data: ProgrammHintRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> ProgrammHintResponse:
    """Create a new Programm hint, appended after the last one in display
    order."""
    hint = programm_hints_service.create_hint(db, data.text)
    return ProgrammHintResponse.model_validate(hint)


@public_content_admin_router.put("/programm-hints/{hint_id}")
def update_programm_hint(
    hint_id: int,
    data: ProgrammHintRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> ProgrammHintResponse:
    """Update a Programm hint's text."""
    hint = programm_hints_service.get_hint_or_404(db, hint_id)
    programm_hints_service.update_hint(db, hint, data.text)
    return ProgrammHintResponse.model_validate(hint)


@public_content_admin_router.post("/programm-hints/{hint_id}/move")
def move_programm_hint(
    hint_id: int,
    data: MoveRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> StatusResponse:
    """Move a Programm hint one position up or down, swapping sort_order
    with its immediate neighbor. A no-op (still 200) if the hint is
    already at that end of the list."""
    hint = programm_hints_service.get_hint_or_404(db, hint_id)
    programm_hints_service.move_hint(db, hint, data.direction)
    return StatusResponse(status="ok")


@public_content_admin_router.delete(
    "/programm-hints/{hint_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_programm_hint(
    hint_id: int,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> None:
    """Permanently delete a Programm hint. No confirmation/undo - the
    admin UI is expected to confirm before calling this."""
    hint = programm_hints_service.get_hint_or_404(db, hint_id)
    programm_hints_service.delete_hint(db, hint)


# --- Quotes -----------------------------------------------------------------


@public_content_admin_router.get("/quotes")
def list_quotes(
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> list[QuoteResponse]:
    """List the testimonial quotes shown on the public site's "Zitate"
    section, in display order."""
    return [
        QuoteResponse.model_validate(quote) for quote in quotes_service.list_quotes(db)
    ]


@public_content_admin_router.post("/quotes", status_code=status.HTTP_201_CREATED)
def create_quote(
    data: QuoteRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> QuoteResponse:
    """Create a new testimonial quote, appended after the last one in
    display order."""
    quote = quotes_service.create_quote(db, data.quote, data.author)
    return QuoteResponse.model_validate(quote)


@public_content_admin_router.put("/quotes/{quote_id}")
def update_quote(
    quote_id: int,
    data: QuoteRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> QuoteResponse:
    """Update a testimonial quote's text and author."""
    quote = quotes_service.get_quote_or_404(db, quote_id)
    quotes_service.update_quote(db, quote, data.quote, data.author)
    return QuoteResponse.model_validate(quote)


@public_content_admin_router.post("/quotes/{quote_id}/move")
def move_quote(
    quote_id: int,
    data: MoveRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> StatusResponse:
    """Move a quote one position up or down, swapping sort_order with its
    immediate neighbor. A no-op (still 200) if the quote is already at
    that end of the list."""
    quote = quotes_service.get_quote_or_404(db, quote_id)
    quotes_service.move_quote(db, quote, data.direction)
    return StatusResponse(status="ok")


@public_content_admin_router.delete(
    "/quotes/{quote_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_quote(
    quote_id: int,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> None:
    """Permanently delete a testimonial quote. No confirmation/undo - the
    admin UI is expected to confirm before calling this."""
    quote = quotes_service.get_quote_or_404(db, quote_id)
    quotes_service.delete_quote(db, quote)


# --- Social links -------------------------------------------------------------


@public_content_admin_router.get("/social-links")
def list_social_links(
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> list[SocialLinkAdminResponse]:
    """List all social media links, enabled and disabled, in display
    order - the admin view. GET /public/site-content (public_site.py)
    returns only the enabled subset."""
    return [
        SocialLinkAdminResponse.model_validate(link)
        for link in social_links_service.list_admin_links(db)
    ]


@public_content_admin_router.post("/social-links", status_code=status.HTTP_201_CREATED)
def create_social_link(
    data: SocialLinkCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> SocialLinkAdminResponse:
    """Create a new social media link, appended after the last one in
    display order. platform is a free, format-checked slug (not a fixed
    set) and can only be set here - see PublicSiteSocialLink's model
    docstring."""
    link = social_links_service.create_link(
        db, data.platform, data.label, data.url, data.is_enabled
    )
    return SocialLinkAdminResponse.model_validate(link)


@public_content_admin_router.put("/social-links/{link_id}")
def update_social_link(
    link_id: uuid.UUID,
    data: SocialLinkUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> SocialLinkAdminResponse:
    """Update a social media link's label, URL, and enabled state.
    platform cannot be changed after creation - delete and recreate
    instead."""
    link = social_links_service.get_link_or_404(db, link_id)
    social_links_service.update_link(db, link, data.label, data.url, data.is_enabled)
    return SocialLinkAdminResponse.model_validate(link)


@public_content_admin_router.post("/social-links/{link_id}/move")
def move_social_link(
    link_id: uuid.UUID,
    data: MoveRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> StatusResponse:
    """Move a social media link one position up or down, swapping
    sort_order with its immediate neighbor. A no-op (still 200) if the
    link is already at that end of the list."""
    link = social_links_service.get_link_or_404(db, link_id)
    social_links_service.move_link(db, link, data.direction)
    return StatusResponse(status="ok")


@public_content_admin_router.delete(
    "/social-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_social_link(
    link_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> None:
    """Permanently delete a social media link. No confirmation/undo - the
    admin UI is expected to confirm before calling this."""
    link = social_links_service.get_link_or_404(db, link_id)
    social_links_service.delete_link(db, link)
