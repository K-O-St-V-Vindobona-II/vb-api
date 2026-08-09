from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.mailer import render_template, send_to_recipients
from app.core.rate_limit import limiter
from app.core.storage import StorageClient, get_storage
from app.db.database import get_db
from app.schemas.base import StatusResponse
from app.schemas.public_content import (
    AboutTabContent,
    AboutTabsResponse,
    ProgrammHintResponse,
    QuoteResponse,
    SiteContentResponse,
    SiteSettingsResponse,
    SocialLinkResponse,
)
from app.schemas.public_gallery import ContactFormRequest, GalleryImagePublicResponse
from app.services import (
    about_tabs_service,
    programm_hints_service,
    public_gallery_service,
    quotes_service,
    site_settings_service,
    social_links_service,
)

# Deliberately NOT auth-guarded anywhere in this router — these endpoints back
# the public www.vindobona2.at marketing site, which has no login at all.
public_site_router = APIRouter()

CONTACT_RECIPIENTS = ["philchc@vindobona2.at", "vindoboneninfo@gmail.com"]


@public_site_router.get("/gallery")
def list_gallery(
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> list[GalleryImagePublicResponse]:
    """Published images for the public gallery section, in display order."""
    images = public_gallery_service.list_public_images(db)
    return [
        GalleryImagePublicResponse(
            id=img.id,
            url=public_gallery_service.get_presigned_url(img, storage),
            caption=img.caption,
            width=img.width,
            height=img.height,
        )
        for img in images
    ]


@public_site_router.get("/site-content")
def get_site_content(
    db: Annotated[Session, Depends(get_db)],
) -> SiteContentResponse:
    """Everything else on the public site that's admin-editable, bundled
    into one response (about tabs, MKV video, programm calendar + hints,
    quotes, social links) - avoids 5 separate requests when the public
    site loads. Gallery stays a separate endpoint (S3 presigned URLs are a
    different cost/latency profile). Exactly 5 SQL statements, no
    relations to lazy-load."""
    tabs = about_tabs_service.get_tabs_by_slot(db)
    return SiteContentResponse(
        about_tabs=AboutTabsResponse(
            anfang=AboutTabContent.model_validate(tabs["anfang"]),
            mkv=AboutTabContent.model_validate(tabs["mkv"]),
            heute=AboutTabContent.model_validate(tabs["heute"]),
        ),
        settings=SiteSettingsResponse.model_validate(
            site_settings_service.get_settings(db)
        ),
        programm_hints=[
            ProgrammHintResponse.model_validate(hint)
            for hint in programm_hints_service.list_hints(db)
        ],
        quotes=[
            QuoteResponse.model_validate(quote)
            for quote in quotes_service.list_quotes(db)
        ],
        social_links=[
            SocialLinkResponse.model_validate(link)
            for link in social_links_service.list_enabled_links(db)
        ],
    )


@public_site_router.post("/contact", status_code=202)
@limiter.limit("5/minute")  # type: ignore[reportUntypedFunctionDecorator]
def submit_contact_form(
    request: Request,  # noqa: ARG001
    data: ContactFormRequest,
) -> StatusResponse:
    """Contact form submission from the public site.

    Rate limit: 5/min per IP. Spam protection is a honeypot field
    (`website`, validated empty in the schema) rather than reCAPTCHA - no
    external dependency, no key provisioning needed.
    """
    html_content = render_template(
        "public_contact_form.html",
        name=data.name,
        email=data.email,
        message=data.message,
    )
    send_to_recipients(
        CONTACT_RECIPIENTS,
        subject=f"Neue Kontaktaufnahme von {data.name}",
        html_content=html_content,
        template_key="public-contact-form",
        reply_to=data.email,
    )
    return StatusResponse(status="ok")
