from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.core.datetime_utils import local_day_bounds_utc, local_today
from app.models.client_user_agent import ClientUserAgent
from app.models.member import Member
from app.models.request_log import RequestLog
from app.models.sent_email import SentEmail
from app.schemas.tracking import (
    ActivityLogDetail,
    ActivityLogItem,
    ActivitySessionItem,
    ActivityStats,
    EmailTemplateStats,
    SentEmailListItem,
)

ACTION_LABELS: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/login"): "Anmeldung",
    ("POST", "/api/auth/logout"): "Abmeldung",
    ("POST", "/api/auth/google"): "Google-Anmeldung",
    ("POST", "/api/auth/google/link"): "Google-Konto verknüpft",
    ("DELETE", "/api/auth/google/link"): "Google-Konto getrennt",
    ("POST", "/api/auth/forgot-password"): "Passwort-Reset angefordert",
    ("POST", "/api/auth/reset-password"): "Passwort zurückgesetzt",
    ("POST", "/api/standesdb/members"): "Mitglied angelegt",
    ("POST", "/api/standesdb/contacts"): "Kontakt angelegt",
    ("POST", "/api/archive/upload"): "Datei hochgeladen",
    ("POST", "/api/p4x/admin/accounts"): "Konto angelegt",
    ("POST", "/api/p4x/admin/categories"): "Kategorie angelegt",
    ("POST", "/api/p4x/admin/category-filters"): "Filter angelegt",
    ("POST", "/api/standesdb/export"): "Export erstellt",
    ("POST", "/api/p4x/admin/fee-config"): "Beitragskonfiguration angelegt",
    ("POST", "/api/p4x/admin/summary"): "Abrechnung erstellt",
    ("POST", "/api/archive/dirs"): "Ordner erstellt",
}

SUBRESOURCE_PATTERNS: list[tuple[str, str, str]] = [
    ("POST", "/images", "Profilbild hochgeladen"),
    ("PUT", "/images/", "Profilbild bearbeitet"),
    ("DELETE", "/images/", "Profilbild gelöscht"),
    ("GET", "/download/", "Datei heruntergeladen (Thumbnail)"),
    ("GET", "/download", "Datei heruntergeladen"),
    ("PATCH", "/restore", "Wiederhergestellt"),
    ("POST", "/receive", "Dateien verschoben"),
    ("POST", "/comments", "Kommentar erstellt"),
    ("DELETE", "/comments/", "Kommentar gelöscht"),
    ("POST", "/import", "Transaktionen importiert"),
    ("POST", "/set-partner", "Partner zugeordnet"),
    ("POST", "/set-category-direct", "Kategorie zugeordnet"),
    ("DELETE", "/unset-category-direct", "Kategoriezuordnung entfernt"),
    ("POST", "/filter2direct", "Filter → Direkt konvertiert"),
]

ACTION_PATTERNS: list[tuple[str, str, str]] = [
    ("GET", "/api/standesdb/members/", "Mitglied angezeigt"),
    ("GET", "/api/standesdb/contacts/", "Kontakt angezeigt"),
    ("GET", "/api/archive/dirs/", "Verzeichnis angezeigt"),
    ("GET", "/api/archive/files/", "Datei angezeigt"),
    ("PUT", "/api/standesdb/members/", "Mitglied bearbeitet"),
    ("PUT", "/api/standesdb/contacts/", "Kontakt bearbeitet"),
    ("DELETE", "/api/standesdb/contacts/", "Kontakt gelöscht"),
    ("DELETE", "/api/archive/dirs/", "Ordner gelöscht"),
    ("PUT", "/api/archive/dirs/", "Ordner bearbeitet"),
    ("PUT", "/api/archive/files/", "Datei bearbeitet"),
    ("DELETE", "/api/archive/files/", "Datei gelöscht"),
    ("PUT", "/api/p4x/admin/accounts/", "Konto bearbeitet"),
    ("DELETE", "/api/p4x/admin/accounts/", "Konto gelöscht"),
    ("PUT", "/api/p4x/admin/categories/", "Kategorie bearbeitet"),
    ("DELETE", "/api/p4x/admin/categories/", "Kategorie gelöscht"),
    ("PUT", "/api/p4x/admin/transactions/", "Transaktion bearbeitet"),
    ("PUT", "/api/p4x/admin/category-filters/", "Filter bearbeitet"),
    ("DELETE", "/api/p4x/admin/category-filters/", "Filter gelöscht"),
    ("DELETE", "/api/p4x/admin/fee-config/", "Beitragskonfiguration gelöscht"),
    ("POST", "/api/p4x/admin/fee-members/", "Beitragsdaten bearbeitet"),
    ("PATCH", "/api/members/me/", "Profil bearbeitet"),
]


FAILED_LOGIN_PATHS = {"/api/auth/login", "/api/auth/google"}


def resolve_action_label(
    method: str,
    path: str,
    response_status: int = 200,
) -> str:
    if response_status == 401 and path in FAILED_LOGIN_PATHS:
        return "Anmeldung fehlgeschlagen"
    key = (method.upper(), path)
    if key in ACTION_LABELS:
        return ACTION_LABELS[key]
    for pat_method, segment, label in SUBRESOURCE_PATTERNS:
        if method.upper() == pat_method and segment in path:
            return label
    for pat_method, pat_prefix, label in ACTION_PATTERNS:
        if method.upper() == pat_method and path.startswith(pat_prefix):
            return label
    return f"{method.upper()} {path}"


# ---------------------------------------------------------------------------
# Sent emails
# ---------------------------------------------------------------------------

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


def get_sent_email_detail(db: Session, email_id: int) -> SentEmail:
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


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


def _member_name_map(db: Session, member_ids: set[int]) -> dict[int, str]:
    if not member_ids:
        return {}
    members = (
        db.query(Member.id, Member.vorname, Member.nachname)
        .filter(Member.id.in_(member_ids))
        .all()
    )
    return {m.id: f"{m.vorname or ''} {m.nachname or ''}".strip() for m in members}


def get_activity_stats(db: Session) -> ActivityStats:
    today_start, _ = local_day_bounds_utc(local_today())

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


def get_activity_sessions(
    db: Session,
    date_str: str | None,
    member_id: int | None,
    page: int,
    page_size: int,
) -> dict[str, list[ActivitySessionItem] | int]:
    """Group matching RequestLog rows into sessions, then paginate the
    resulting session list. Pagination can't apply to the raw log query
    itself - cutting it off mid-page would split a session across pages,
    since sessions are derived from consecutive rows for the same member.
    The date/member filters already bound the row count fetched here."""
    query = db.query(RequestLog).filter(RequestLog.member_id.isnot(None))

    if date_str:
        try:
            day = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Format: YYYY-MM-DD") from None
        day_start, day_end = local_day_bounds_utc(day)
        query = query.filter(
            RequestLog.created_at >= day_start,
            RequestLog.created_at < day_end,
        )
    else:
        today_start, _ = local_day_bounds_utc(local_today())
        query = query.filter(RequestLog.created_at >= today_start)

    if member_id:
        query = query.filter(RequestLog.member_id == member_id)

    logs = query.order_by(RequestLog.created_at).all()

    if not logs:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    all_member_ids = {log.member_id for log in logs if log.member_id}
    names = _member_name_map(db, all_member_ids)

    sessions = _group_logs_into_sessions(logs, names)
    total = len(sessions)
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "items": sessions[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_activity_detail(db: Session, log_id: int) -> ActivityLogDetail:
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


def list_activity(
    db: Session,
    page: int,
    page_size: int,
    *,
    member_id: int | None,
    date_from: str | None,
    date_to: str | None,
) -> dict[str, list[ActivityLogItem] | int]:
    query = db.query(RequestLog)

    if member_id:
        query = query.filter(RequestLog.member_id == member_id)

    if date_from:
        try:
            d_start, _ = local_day_bounds_utc(date.fromisoformat(date_from))
            query = query.filter(RequestLog.created_at >= d_start)
        except ValueError:
            pass

    if date_to:
        try:
            _, d_end = local_day_bounds_utc(date.fromisoformat(date_to))
            query = query.filter(RequestLog.created_at < d_end)
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
