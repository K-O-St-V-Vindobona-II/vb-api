import json
from datetime import date
from typing import TYPE_CHECKING

import jwt
from fastapi import HTTPException

from app.core.datetime_utils import get_app_timezone, local_day_bounds_utc
from app.core.security import ALGORITHM, SECRET_KEY
from app.models.client_user_agent import ClientUserAgent
from app.models.member import Member
from app.models.request_log import RequestLog
from app.schemas.activity_log import (
    ActivityDayGroup,
    ActivityLogDetail,
    ActivityLogEntry,
    ActivityMemberDayDetail,
)

if TYPE_CHECKING:
    import uuid

    from pydantic import JsonValue
    from sqlalchemy.orm import Session
from app.schemas.base import IdLabelOption

REDACT_KEYS = frozenset(
    {
        "password",
        "password_confirmation",
        "current_password",
        "new_password",
        "access_token",
        "refresh_token",
        "token",
        "secret",
        "authorization",
    }
)
_REDACTED_VALUE = "***"

# Bounds how much of a request/response body ends up permanently stored in
# this audit table - a handful of admin exports return payloads well beyond
# what anyone ever needs to inspect in the log viewer, and this table has no
# other size limit besides the scheduled TRACKING_RETENTION_MONTHS purge.
_MAX_STORED_JSON_CHARS = 65_536

_SKIP_LOG_HEADER = "x-skip-request-log"
_SKIP_PATHS = frozenset({"/", "/docs", "/openapi.json"})


def should_skip(path: str, *, skip_header_present: bool) -> bool:
    return skip_header_present or path in _SKIP_PATHS


def redact(value: JsonValue) -> JsonValue:
    """Recursively masks any dict key matching REDACT_KEYS (case-insensitive)
    in both request and response payloads, at any nesting depth. Returns a
    new structure, never mutates the input."""
    if isinstance(value, dict):
        return {
            key: _REDACTED_VALUE if key.lower() in REDACT_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def cap_stored_size(value: JsonValue) -> JsonValue:
    if value is None:
        return None
    if len(json.dumps(value, ensure_ascii=False)) > _MAX_STORED_JSON_CHARS:
        return None
    return value


def _email_from_token(token: str) -> str | None:
    try:
        payload = jwt.decode(
            token, SECRET_KEY, algorithms=[ALGORITHM], options={"verify_exp": False}
        )
    except jwt.PyJWTError:
        return None
    email = payload.get("sub")
    return email if isinstance(email, str) else None


def _resolve_member_id(db: Session, auth_header: str | None) -> uuid.UUID | None:
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None
    email = _email_from_token(auth_header[len("Bearer ") :])
    if email is None:
        return None
    member = db.query(Member.id).filter(Member.email == email).first()
    return member.id if member else None


def _get_or_create_client_user_agent(db: Session, ua_string: str) -> uuid.UUID:
    existing = (
        db.query(ClientUserAgent).filter(ClientUserAgent.string == ua_string).first()
    )
    if existing:
        return existing.id
    new_ua = ClientUserAgent(string=ua_string)
    db.add(new_ua)
    db.flush()
    return new_ua.id


def record_request(
    db: Session,
    *,
    client_ip: str,
    client_ips: list[str],
    auth_header: str | None,
    user_agent_string: str | None,
    request_method: str,
    request_path: str,
    request_input: JsonValue,
    response_status: int,
    response_content: JsonValue,
    memory_usage: int,
) -> None:
    """The one synchronous, DB-touching unit of work per request - always
    invoked through starlette.concurrency.run_in_threadpool() by the
    middleware so it never blocks the event loop."""
    member_id = _resolve_member_id(db, auth_header)
    client_user_agent_id = (
        _get_or_create_client_user_agent(db, user_agent_string)
        if user_agent_string
        else None
    )
    db.add(
        RequestLog(
            client_ip=client_ip,
            client_ips=client_ips,
            client_user_agent_id=client_user_agent_id,
            member_id=member_id,
            request_method=request_method,
            request_path=request_path,
            request_input=redact(cap_stored_size(request_input)),
            response_status=response_status,
            response_content=redact(cap_stored_size(response_content)),
            memory_usage=memory_usage,
        )
    )
    db.commit()


def _member_label(vorname: str | None, nachname: str | None) -> str:
    return f"{vorname or ''} {nachname or ''}".strip()


def list_days_with_activity(
    db: Session, year: int, month: int
) -> list[ActivityDayGroup]:
    """Groups the month's activity by local (Settings.app_timezone)
    calendar day, newest day first - N+1-safe in exactly 2 queries
    regardless of day/member count (batch-load then assemble in Python)."""
    first_of_month = date(year, month, 1)
    first_of_next_month = (
        date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    )
    month_start_utc, _ = local_day_bounds_utc(first_of_month)
    month_end_utc, _ = local_day_bounds_utc(first_of_next_month)

    rows = (
        db.query(RequestLog.created_at, RequestLog.member_id)
        .filter(
            RequestLog.created_at >= month_start_utc,
            RequestLog.created_at < month_end_utc,
            RequestLog.member_id.isnot(None),
        )
        .all()
    )
    if not rows:
        return []

    tz = get_app_timezone()
    member_ids_by_day: dict[date, set[uuid.UUID]] = {}
    for created_at, member_id in rows:
        if member_id is None:
            continue
        local_day = created_at.astimezone(tz).date()
        member_ids_by_day.setdefault(local_day, set()).add(member_id)

    all_member_ids = {mid for ids in member_ids_by_day.values() for mid in ids}
    members_by_id = {
        m.id: m for m in db.query(Member).filter(Member.id.in_(all_member_ids)).all()
    }

    return [
        ActivityDayGroup(
            day=day,
            members=[
                IdLabelOption(
                    id=str(member.id),
                    label=_member_label(member.vorname, member.nachname),
                )
                for member in sorted(
                    (
                        members_by_id[mid]
                        for mid in member_ids_by_day[day]
                        if mid in members_by_id
                    ),
                    key=lambda m: (m.nachname or "", m.vorname or ""),
                )
            ],
        )
        for day in sorted(member_ids_by_day, reverse=True)
    ]


def list_entries_for_member_day(
    db: Session, member_id: uuid.UUID, day: date
) -> ActivityMemberDayDetail:
    member = db.query(Member).filter(Member.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Mitglied nicht gefunden")

    start_utc, end_utc = local_day_bounds_utc(day)
    entries = (
        db.query(RequestLog)
        .filter(
            RequestLog.member_id == member_id,
            RequestLog.created_at >= start_utc,
            RequestLog.created_at < end_utc,
        )
        .order_by(RequestLog.created_at)
        .all()
    )
    return ActivityMemberDayDetail(
        member_name=_member_label(member.vorname, member.nachname),
        entries=[
            ActivityLogEntry(
                id=entry.id,
                created_at=entry.created_at,
                request_method=entry.request_method,
                request_path=entry.request_path,
            )
            for entry in entries
        ],
    )


def get_entry(db: Session, log_id: uuid.UUID) -> ActivityLogDetail:
    log = db.query(RequestLog).filter(RequestLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Log-Eintrag nicht gefunden")

    member_name = None
    if log.member_id:
        member = db.query(Member).filter(Member.id == log.member_id).first()
        if member:
            member_name = _member_label(member.vorname, member.nachname)

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
        client_ip=log.client_ip,
        client_ips=log.client_ips,
        client_user_agent=ua_string,
        member_id=log.member_id,
        member_name=member_name,
        request_method=log.request_method,
        request_path=log.request_path,
        request_input=log.request_input,
        response_status=log.response_status,
        response_content=log.response_content,
        memory_usage=log.memory_usage,
        created_at=log.created_at,
    )
