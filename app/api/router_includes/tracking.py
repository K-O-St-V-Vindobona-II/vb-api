from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.auth_guards import require_permission
from app.core.mailer import render_template
from app.core.tasks import TRACKING_RETENTION_MONTHS
from app.db.database import get_db
from app.models.member import Member
from app.models.sent_email import SentEmail
from app.schemas.tracking import (
    ActivityLogDetail,
    ActivityLogItem,
    ActivitySessionItem,
    ActivityStats,
    EmailTemplatePreview,
    EmailTemplateStats,
    SentEmailDetail,
    SentEmailListItem,
)
from app.services import tracking_service

tracking_router = APIRouter()


@tracking_router.get("/config")
def get_tracking_config(
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> dict[str, int]:
    """Return the tracking data retention period in months."""
    return {"retention_months": TRACKING_RETENTION_MONTHS}


def _format_preview_value(
    _key: str,
    value: str | None,
    _diff: dict[str, dict[str, str | None]],
) -> str:
    return value if value is not None else "-"


TEMPLATE_PREVIEW_DATA: dict[str, dict[str, object]] = {
    "password-reset": {
        "reset_link": "https://intern.vindobona2.at/reset-password?token=abc123",
    },
    "entry-changed": {
        "modifier_cn": "Max Mustermann v/o Testikus",
        "entry_type": "member",
        "entry_cn": "Franz Beispiel v/o Musterknabe",
        "change_type": "update",
        "diff": {
            "nachname": {"old": "Beispiel", "new": "Beispiel-Neu"},
            "email": {"old": "alt@example.com", "new": "neu@example.com"},
        },
        "format_value": _format_preview_value,
    },
    "member-change-request-submitted": {
        "member_cn": "Franz Beispiel v/o Musterknabe",
        "diff": {
            "nachname": {"old": "Beispiel", "new": "Beispiel-Neu"},
            "email": {"old": "alt@example.com", "new": "neu@example.com"},
        },
        "format_value": _format_preview_value,
    },
    "member-change-request-resolved": {
        "approved": {
            "email": {"old": "alt@example.com", "new": "neu@example.com"},
        },
        "rejected": {
            "nachname": {"old": "Beispiel", "new": "Beispiel-Neu"},
        },
        "format_value": _format_preview_value,
    },
    "birthday": {
        "name": "Max Mustermann v/o Testikus",
        "age": 25,
    },
    "debtor_reminder": {
        "name": "Max Mustermann v/o Testikus",
        "fee": "15,00",
        "debt": "90,00",
        "target": "30. Juni 2026",
        "sender_name": "Franz Beispiel v/o Musterknabe",
    },
    "chronicles": {
        "start": "30. Juni 2026",
        "end": "6. Juli 2026",
        "anniversaries": {
            "vbw": {
                "lebend": {
                    "geburtsdatum": [
                        {
                            "cn": "Max Mustermann v/o Testikus",
                            "date": "1. Juli",
                            "years": 50,
                        },
                    ],
                    "aufnahmedatum": [
                        {
                            "cn": "Franz Beispiel v/o Musterknabe",
                            "date": "3. Juli",
                            "years": 10,
                        },
                    ],
                },
            },
        },
    },
    "archive_health_check": {
        "missing": ["archive/store/abc123"],
        "orphans": ["archive/store/def456"],
        "unsorted_count": 3,
    },
    "standesdb_health_check": {
        "missing": [],
        "orphans": ["standesdb/images/def456"],
    },
    "public-contact-form": {
        "name": "Max Mustermann",
        "email": "max@example.com",
        "message": "Hallo, ich interessiere mich für Vindobona II.",
    },
}


@tracking_router.get("/sent-emails/templates")
def get_email_templates(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> list[EmailTemplateStats]:
    """List all registered email templates with send counts and last-sent timestamps."""
    return tracking_service.get_email_templates(db)


@tracking_router.get("/sent-emails/templates/{template_key}/preview")
def get_email_template_preview(
    template_key: str,
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> EmailTemplatePreview:
    """Render an email template with dummy data for live preview."""
    entry = next(
        (
            t
            for t in tracking_service.EMAIL_TEMPLATE_REGISTRY
            if t["key"] == template_key
        ),
        None,
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Template nicht gefunden")

    preview_data = TEMPLATE_PREVIEW_DATA.get(template_key)
    if not preview_data:
        raise HTTPException(
            status_code=404,
            detail="Keine Vorschaudaten verfügbar",
        )

    html = render_template(entry["file"], **preview_data)
    return EmailTemplatePreview(
        template_key=template_key,
        template_name=entry["name"],
        html=html,
    )


@tracking_router.get("/sent-emails/{email_id}", response_model=SentEmailDetail)
def get_sent_email_detail(
    email_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> SentEmail:
    """Return full details of a sent email including body and headers."""
    return tracking_service.get_sent_email_detail(db, email_id)


@tracking_router.get("/sent-emails", response_model=dict)
def list_sent_emails(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    year: int | None = None,
    month: int | None = None,
    search: str | None = None,
) -> dict[str, list[SentEmailListItem] | int]:
    """List sent emails with optional year/month and text search filters (paginated)."""
    return tracking_service.list_sent_emails(db, page, page_size, year, month, search)


@tracking_router.get("/activity/stats")
def get_activity_stats(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> ActivityStats:
    """Return today's activity summary: active users, actions, breakdown."""
    return tracking_service.get_activity_stats(db)


@tracking_router.get("/activity/sessions", response_model=dict)
def get_activity_sessions(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
    date_str: str | None = None,
    member_id: int | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, list[ActivitySessionItem] | int]:
    """Return user activity grouped into sessions (30-min gap = new session),
    paginated."""
    return tracking_service.get_activity_sessions(
        db, date_str, member_id, page, page_size
    )


@tracking_router.get("/activity/{log_id}")
def get_activity_detail(
    log_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> ActivityLogDetail:
    """Return full details of a single activity log entry."""
    return tracking_service.get_activity_detail(db, log_id)


@tracking_router.get("/activity", response_model=dict)
def list_activity(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    member_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, list[ActivityLogItem] | int]:
    """List activity log entries with optional filters (paginated)."""
    return tracking_service.list_activity(
        db, page, page_size, member_id, date_from, date_to
    )
