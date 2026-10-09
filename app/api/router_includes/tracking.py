import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.auth_guards import RequireSystemAdmin
from app.core.config import get_settings
from app.db.database import get_db
from app.schemas.base import PaginatedResponse
from app.schemas.tracking import (
    EmailTemplatePreview,
    EmailTemplateStats,
    SentEmailDetail,
    SentEmailListItem,
    TrackingConfigResponse,
)
from app.services import email_preview_service, tracking_service

tracking_router = APIRouter()


@tracking_router.get("/config")
def get_tracking_config(
    _user: RequireSystemAdmin,
) -> TrackingConfigResponse:
    """Return the tracking data retention period in months."""
    return TrackingConfigResponse(
        retention_months=get_settings().tracking_retention_months
    )


@tracking_router.get("/sent-emails/templates")
def get_email_templates(
    db: Annotated[Session, Depends(get_db)],
    _user: RequireSystemAdmin,
) -> list[EmailTemplateStats]:
    """List all registered email templates with send counts and last-sent timestamps."""
    return tracking_service.get_email_templates(db)


@tracking_router.get("/sent-emails/templates/{template_key}/preview")
def get_email_template_preview(
    template_key: str,
    _user: RequireSystemAdmin,
) -> EmailTemplatePreview:
    """Render an email template with dummy data for live preview."""
    return email_preview_service.build_preview(template_key)


@tracking_router.get("/sent-emails/{email_id}")
def get_sent_email_detail(
    email_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: RequireSystemAdmin,
) -> SentEmailDetail:
    """Return full details of a sent email including body and headers."""
    return tracking_service.get_sent_email_detail(db, email_id)


@tracking_router.get("/sent-emails")
def list_sent_emails(
    db: Annotated[Session, Depends(get_db)],
    *,
    _user: RequireSystemAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    month: Annotated[int | None, Query(ge=1, le=12)] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> PaginatedResponse[SentEmailListItem]:
    """List sent emails with optional year/month and text search filters (paginated)."""
    return PaginatedResponse[SentEmailListItem].model_validate(
        tracking_service.list_sent_emails(
            db, page, page_size, year=year, month=month, search=search
        )
    )
