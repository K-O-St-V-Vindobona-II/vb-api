from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.auth_guards import require_permission
from app.db.database import get_db
from app.models.member import Member
from app.schemas.base import MoveRequest
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
    tab = about_tabs_service.get_tab_or_404(db, slot)
    about_tabs_service.update_tab(db, tab, data.title, data.body)
    return AboutTabAdminResponse.model_validate(tab)


# --- Site settings (video + calendar) --------------------------------------


@public_content_admin_router.get("/settings")
def get_settings(
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> SiteSettingsResponse:
    return SiteSettingsResponse.model_validate(site_settings_service.get_settings(db))


@public_content_admin_router.put("/settings")
def update_settings(
    data: SiteSettingsUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> SiteSettingsResponse:
    settings = site_settings_service.get_settings(db)
    site_settings_service.update_settings(
        db,
        settings,
        data.about_video_heading,
        data.youtube_url,
        data.calendar_id,
        data.gallery_heading,
    )
    return SiteSettingsResponse.model_validate(settings)


# --- Programm hints ---------------------------------------------------------


@public_content_admin_router.get("/programm-hints")
def list_programm_hints(
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> list[ProgrammHintResponse]:
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
    hint = programm_hints_service.create_hint(db, data.text)
    return ProgrammHintResponse.model_validate(hint)


@public_content_admin_router.put("/programm-hints/{hint_id}")
def update_programm_hint(
    hint_id: int,
    data: ProgrammHintRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> ProgrammHintResponse:
    hint = programm_hints_service.get_hint_or_404(db, hint_id)
    programm_hints_service.update_hint(db, hint, data.text)
    return ProgrammHintResponse.model_validate(hint)


@public_content_admin_router.post("/programm-hints/{hint_id}/move")
def move_programm_hint(
    hint_id: int,
    data: MoveRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> dict[str, str]:
    hint = programm_hints_service.get_hint_or_404(db, hint_id)
    programm_hints_service.move_hint(db, hint, data.direction)
    return {"status": "ok"}


@public_content_admin_router.delete(
    "/programm-hints/{hint_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_programm_hint(
    hint_id: int,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> None:
    hint = programm_hints_service.get_hint_or_404(db, hint_id)
    programm_hints_service.delete_hint(db, hint)


# --- Quotes -----------------------------------------------------------------


@public_content_admin_router.get("/quotes")
def list_quotes(
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> list[QuoteResponse]:
    return [
        QuoteResponse.model_validate(quote) for quote in quotes_service.list_quotes(db)
    ]


@public_content_admin_router.post("/quotes", status_code=status.HTTP_201_CREATED)
def create_quote(
    data: QuoteRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> QuoteResponse:
    quote = quotes_service.create_quote(db, data.quote, data.author)
    return QuoteResponse.model_validate(quote)


@public_content_admin_router.put("/quotes/{quote_id}")
def update_quote(
    quote_id: int,
    data: QuoteRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> QuoteResponse:
    quote = quotes_service.get_quote_or_404(db, quote_id)
    quotes_service.update_quote(db, quote, data.quote, data.author)
    return QuoteResponse.model_validate(quote)


@public_content_admin_router.post("/quotes/{quote_id}/move")
def move_quote(
    quote_id: int,
    data: MoveRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> dict[str, str]:
    quote = quotes_service.get_quote_or_404(db, quote_id)
    quotes_service.move_quote(db, quote, data.direction)
    return {"status": "ok"}


@public_content_admin_router.delete(
    "/quotes/{quote_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_quote(
    quote_id: int,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> None:
    quote = quotes_service.get_quote_or_404(db, quote_id)
    quotes_service.delete_quote(db, quote)


# --- Social links -------------------------------------------------------------


@public_content_admin_router.get("/social-links")
def list_social_links(
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> list[SocialLinkAdminResponse]:
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
    link = social_links_service.create_link(
        db, data.platform, data.label, data.url, data.is_enabled
    )
    return SocialLinkAdminResponse.model_validate(link)


@public_content_admin_router.put("/social-links/{link_id}")
def update_social_link(
    link_id: int,
    data: SocialLinkUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> SocialLinkAdminResponse:
    link = social_links_service.get_link_or_404(db, link_id)
    social_links_service.update_link(db, link, data.label, data.url, data.is_enabled)
    return SocialLinkAdminResponse.model_validate(link)


@public_content_admin_router.post("/social-links/{link_id}/move")
def move_social_link(
    link_id: int,
    data: MoveRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> dict[str, str]:
    link = social_links_service.get_link_or_404(db, link_id)
    social_links_service.move_link(db, link, data.direction)
    return {"status": "ok"}


@public_content_admin_router.delete(
    "/social-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_social_link(
    link_id: int,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> None:
    link = social_links_service.get_link_or_404(db, link_id)
    social_links_service.delete_link(db, link)
