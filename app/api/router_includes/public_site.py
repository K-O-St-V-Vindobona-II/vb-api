from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.mailer import render_template, send_to_recipients
from app.core.rate_limit import limiter
from app.core.storage import StorageClient, get_storage
from app.db.database import get_db
from app.schemas.public_gallery import ContactFormRequest, GalleryImagePublicResponse
from app.services import public_gallery_service

# Deliberately NOT auth-guarded anywhere in this router — these endpoints back
# the public www.vindobona2.at marketing site, which has no login at all.
public_site_router = APIRouter()

CONTACT_RECIPIENTS = ["log@gebruederpixel.at", "vindoboneninfo@gmail.com"]


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


@public_site_router.post("/contact", status_code=202)
@limiter.limit("5/minute")  # type: ignore[reportUntypedFunctionDecorator]
def submit_contact_form(
    request: Request,  # noqa: ARG001
    data: ContactFormRequest,
) -> dict[str, str]:
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
    return {"status": "ok"}
