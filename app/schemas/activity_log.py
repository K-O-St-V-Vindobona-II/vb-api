import uuid
from datetime import date

from pydantic import BaseModel, JsonValue

from app.schemas.base import IdLabelOption, UtcDatetime


class ActivityDayGroup(BaseModel):
    """One calendar day (local app timezone) within the requested month
    that had at least one activity log entry, with the members active
    that day."""

    day: date
    members: list[IdLabelOption]


class ActivityLogEntry(BaseModel):
    id: uuid.UUID
    created_at: UtcDatetime
    request_method: str
    request_path: str


class ActivityMemberDayDetail(BaseModel):
    member_name: str
    entries: list[ActivityLogEntry]


class ActivityLogDetail(BaseModel):
    id: uuid.UUID
    client_ip: str
    client_ips: list[str]
    client_user_agent: str | None
    member_id: uuid.UUID | None
    member_name: str | None
    request_method: str
    request_path: str
    request_input: JsonValue | None
    response_status: int
    response_content: JsonValue | None
    memory_usage: int
    created_at: UtcDatetime
