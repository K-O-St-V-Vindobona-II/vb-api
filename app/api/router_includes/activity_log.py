import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.auth_guards import RequireSystemAdmin
from app.db.database import get_db
from app.schemas.activity_log import (
    ActivityDayGroup,
    ActivityLogDetail,
    ActivityMemberDayDetail,
)
from app.services import activity_log_service

activity_log_router = APIRouter()


@activity_log_router.get("")
def list_days_with_activity(
    year: Annotated[int, Query(ge=2000, le=2100)],
    month: Annotated[int, Query(ge=1, le=12)],
    db: Annotated[Session, Depends(get_db)],
    _user: RequireSystemAdmin,
) -> list[ActivityDayGroup]:
    """List calendar days in the given month that had at least one logged
    request, each with the members active that day."""
    return activity_log_service.list_days_with_activity(db, year, month)


@activity_log_router.get("/members/{member_id}")
def get_activity_for_member_day(
    member_id: uuid.UUID,
    day: Annotated[date, Query()],
    db: Annotated[Session, Depends(get_db)],
    _user: RequireSystemAdmin,
) -> ActivityMemberDayDetail:
    """List a single member's logged requests for a single calendar day."""
    return activity_log_service.list_entries_for_member_day(db, member_id, day)


@activity_log_router.get("/{log_id}")
def get_activity_entry(
    log_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: RequireSystemAdmin,
) -> ActivityLogDetail:
    """Return full details of a single activity log entry."""
    return activity_log_service.get_entry(db, log_id)
