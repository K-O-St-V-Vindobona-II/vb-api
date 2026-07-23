from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.api.auth_guards import require_permission
from app.core.mailer import render_template
from app.core.tasks import TRACKING_RETENTION_MONTHS
from app.db.database import get_db
from app.models.client_user_agent import ClientUserAgent
from app.models.member import Member
from app.models.request_log import RequestLog
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
from app.services.tracking_service import resolve_action_label

tracking_router = APIRouter()


@tracking_router.get("/config")
def get_tracking_config(
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> dict[str, int]:
    """Return the tracking data retention period in months."""
    return {"retention_months": TRACKING_RETENTION_MONTHS}


def _member_name_map(db: Session, member_ids: set[int]) -> dict[int, str]:
    if not member_ids:
        return {}
    members = (
        db.query(Member.id, Member.vorname, Member.nachname)
        .filter(Member.id.in_(member_ids))
        .all()
    )
    return {m.id: f"{m.vorname or ''} {m.nachname or ''}".strip() for m in members}


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


@tracking_router.get("/sent-emails/templates/{template_key}/preview")
def get_email_template_preview(
    template_key: str,
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> EmailTemplatePreview:
    """Render an email template with dummy data for live preview."""
    entry = next(
        (t for t in EMAIL_TEMPLATE_REGISTRY if t["key"] == template_key),
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
    email = db.query(SentEmail).filter(SentEmail.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email nicht gefunden")
    return email


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
    query = db.query(SentEmail)

    if year and month:
        start = datetime(year, month, 1, tzinfo=UTC)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=UTC)
        else:
            end = datetime(year, month + 1, 1, tzinfo=UTC)
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


@tracking_router.get("/activity/stats")
def get_activity_stats(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> ActivityStats:
    """Return today's activity summary: active users, actions, breakdown."""
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    today_logs = db.query(RequestLog).filter(RequestLog.created_at >= today_start).all()

    active_users = {log.member_id for log in today_logs if log.member_id}
    actions_by_type: dict[str, int] = {}
    for log in today_logs:
        label = resolve_action_label(
            log.request_method, log.request_path, log.response_status
        )
        actions_by_type[label] = actions_by_type.get(label, 0) + 1

    return ActivityStats(
        active_users_today=len(active_users),
        total_actions_today=len(today_logs),
        actions_by_type=actions_by_type,
    )


def _is_session_boundary(
    log: RequestLog,
    current_member: int | None,
    current_group: list[RequestLog],
    session_gap: timedelta,
) -> bool:
    if current_member != log.member_id:
        return True
    if not current_group:
        return False
    return bool(
        log.created_at
        and current_group[-1].created_at
        and (log.created_at - current_group[-1].created_at) > session_gap
    )


def _group_logs_into_sessions(
    logs: list[RequestLog],
    names: dict[int, str],
) -> list[ActivitySessionItem]:
    session_gap = timedelta(minutes=30)
    sessions: list[ActivitySessionItem] = []
    current_group: list[RequestLog] = []
    current_member: int | None = None

    for log in logs:
        if _is_session_boundary(log, current_member, current_group, session_gap):
            if current_group and current_member:
                sessions.append(_build_session(current_group, current_member, names))
            current_group = []
            current_member = log.member_id
        current_group.append(log)

    if current_group and current_member:
        sessions.append(_build_session(current_group, current_member, names))

    return sessions


@tracking_router.get("/activity/sessions")
def get_activity_sessions(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
    date_str: str | None = None,
    member_id: int | None = None,
) -> list[ActivitySessionItem]:
    """Return user activity grouped into sessions (30-min gap = new session)."""
    query = db.query(RequestLog).filter(RequestLog.member_id.isnot(None))

    if date_str:
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            raise HTTPException(status_code=400, detail="Format: YYYY-MM-DD") from None
        query = query.filter(
            RequestLog.created_at >= day,
            RequestLog.created_at < day + timedelta(days=1),
        )
    else:
        today_start = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        query = query.filter(RequestLog.created_at >= today_start)

    if member_id:
        query = query.filter(RequestLog.member_id == member_id)

    logs = query.order_by(RequestLog.created_at).all()

    if not logs:
        return []

    all_member_ids = {log.member_id for log in logs if log.member_id}
    names = _member_name_map(db, all_member_ids)

    return _group_logs_into_sessions(logs, names)


def _build_session(
    logs: list[RequestLog],
    member_id: int,
    names: dict[int, str],
) -> ActivitySessionItem:
    now = datetime.now(UTC)
    return ActivitySessionItem(
        member_id=member_id,
        member_name=names.get(member_id, f"User #{member_id}"),
        started_at=logs[0].created_at or now,
        ended_at=logs[-1].created_at or now,
        action_count=len(logs),
        actions=[
            ActivityLogItem(
                id=log.id,
                created_at=log.created_at,
                member_id=log.member_id,
                action_label=resolve_action_label(
                    log.request_method,
                    log.request_path,
                    log.response_status,
                ),
                request_method=log.request_method,
                request_path=log.request_path,
                response_status=log.response_status,
                client_ip=log.client_ip,
            )
            for log in logs
        ],
    )


@tracking_router.get("/activity/{log_id}")
def get_activity_detail(
    log_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> ActivityLogDetail:
    """Return full details of a single activity log entry."""
    log = db.query(RequestLog).filter(RequestLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Log-Eintrag nicht gefunden")

    member_name = None
    if log.member_id:
        names = _member_name_map(db, {log.member_id})
        member_name = names.get(log.member_id)

    ua_string = None
    if log.client_user_agent_id:
        ua = (
            db.query(ClientUserAgent)
            .filter(ClientUserAgent.id == log.client_user_agent_id)
            .first()
        )
        if ua:
            ua_string = ua.string

    return ActivityLogDetail(
        id=log.id,
        created_at=log.created_at,
        member_id=log.member_id,
        member_name=member_name,
        action_label=resolve_action_label(
            log.request_method, log.request_path, log.response_status
        ),
        request_method=log.request_method,
        request_path=log.request_path,
        response_status=log.response_status,
        client_ip=log.client_ip,
        request_input=log.request_input,
        response_content=log.response_content,
        client_user_agent=ua_string,
    )


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
    query = db.query(RequestLog)

    if member_id:
        query = query.filter(RequestLog.member_id == member_id)

    if date_from:
        try:
            d = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=UTC)
            query = query.filter(RequestLog.created_at >= d)
        except ValueError:
            pass

    if date_to:
        try:
            d = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=UTC)
            query = query.filter(RequestLog.created_at < d + timedelta(days=1))
        except ValueError:
            pass

    total = query.count()
    logs = (
        query.order_by(desc(RequestLog.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    all_member_ids = {log.member_id for log in logs if log.member_id}
    names = _member_name_map(db, all_member_ids)

    items = [
        ActivityLogItem(
            id=log.id,
            created_at=log.created_at,
            member_id=log.member_id,
            member_name=names.get(log.member_id) if log.member_id else None,
            action_label=resolve_action_label(
                log.request_method, log.request_path, log.response_status
            ),
            request_method=log.request_method,
            request_path=log.request_path,
            response_status=log.response_status,
            client_ip=log.client_ip,
        )
        for log in logs
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
