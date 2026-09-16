from datetime import date, datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import desc, func

from app.core.datetime_utils import local_day_bounds_utc
from app.models.sent_email import SentEmail
from app.schemas.tracking import EmailTemplateStats, SentEmailListItem

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

EMAIL_TEMPLATE_REGISTRY: list[dict[str, str]] = [
    {
        "key": "password-reset",
        "name": "Passwort zurücksetzen",
        "source": "mailer.py → send_reset_email()",
        "file": "password_reset.html",
    },
    {
        "key": "entry-changed",
        "name": "Datenbankänderung",
        "source": "mailer.py → send_entry_changed_email()",
        "file": "entry_changed.html",
    },
    {
        "key": "member-change-request-submitted",
        "name": "Neuer Änderungsantrag (an Admin)",
        "source": "mailer.py → send_member_change_request_submitted_email()",
        "file": "member_change_request_submitted.html",
    },
    {
        "key": "member-change-request-resolved",
        "name": "Änderungsantrag entschieden (an Mitglied)",
        "source": "mailer.py → send_member_change_request_resolved_email()",
        "file": "member_change_request_resolved.html",
    },
    {
        "key": "own-image-changed",
        "name": "Profilbild-Selbstverwaltung (an Org-Admin)",
        "source": "mailer.py → send_own_image_changed_email()",
        "file": "own_image_changed.html",
    },
    {
        "key": "birthday",
        "name": "Geburtstagsgrüße",
        "source": "scheduler.py → job_birthday_mails()",
        "file": "birthday.html",
    },
    {
        "key": "debtor_reminder",
        "name": "Schuldner-Erinnerung",
        "source": "scheduler.py → job_debtor_reminder()",
        "file": "debtor_reminder.html",
    },
    {
        "key": "chronicles",
        "name": "Standesdb-Chronik",
        "source": "scheduler.py → job_standesdb_chronicles()",
        "file": "chronicles.html",
    },
    {
        "key": "archive_health_check",
        "name": "Archiv-Konsistenzprüfung",
        "source": "scheduler.py → job_archive_health_check()",
        "file": "archive_health_check.html",
    },
    {
        "key": "standesdb_health_check",
        "name": "Standesdb-Konsistenzprüfung",
        "source": "scheduler.py → job_standesdb_health_check()",
        "file": "standesdb_health_check.html",
    },
    {
        "key": "public-contact-form",
        "name": "Kontaktformular (www.vindobona2.at)",
        "source": "public_site.py → submit_contact_form()",
        "file": "public_contact_form.html",
    },
]


def get_sent_email_detail(db: Session, email_id: uuid.UUID) -> SentEmail:
    email = db.query(SentEmail).filter(SentEmail.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email nicht gefunden")
    return email


def list_sent_emails(
    db: Session,
    page: int,
    page_size: int,
    *,
    year: int | None,
    month: int | None,
    search: str | None,
) -> dict[str, list[SentEmailListItem] | int]:
    query = db.query(SentEmail)

    if year and month:
        start, _ = local_day_bounds_utc(date(year, month, 1))
        if month == 12:
            end, _ = local_day_bounds_utc(date(year + 1, 1, 1))
        else:
            end, _ = local_day_bounds_utc(date(year, month + 1, 1))
        query = query.filter(
            SentEmail.created_at >= start,
            SentEmail.created_at < end,
        )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            SentEmail.subject.ilike(pattern) | SentEmail.to.ilike(pattern)
        )

    total = query.count()
    items = (
        query.order_by(desc(SentEmail.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "items": [SentEmailListItem.model_validate(e) for e in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_email_templates(db: Session) -> list[EmailTemplateStats]:
    registry_keys = [t["key"] for t in EMAIL_TEMPLATE_REGISTRY]
    rows = (
        db.query(
            SentEmail.headers,
            func.count(SentEmail.id).label("cnt"),
            func.max(SentEmail.created_at).label("last_sent"),
        )
        .filter(SentEmail.headers.in_(registry_keys))
        .group_by(SentEmail.headers)
        .all()
    )
    counts: dict[str, tuple[int, datetime | None]] = {
        row.headers: (row.cnt, row.last_sent) for row in rows
    }
    return [
        EmailTemplateStats(
            template_key=t["key"],
            template_name=t["name"],
            source_location=t["source"],
            count=counts.get(t["key"], (0, None))[0],
            last_sent=counts.get(t["key"], (0, None))[1],
        )
        for t in EMAIL_TEMPLATE_REGISTRY
    ]
