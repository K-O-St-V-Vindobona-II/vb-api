import uuid

from pydantic import BaseModel

from app.schemas.base import UtcDatetime


class SentEmailListItem(BaseModel):
    id: uuid.UUID
    created_at: UtcDatetime | None
    to: str | None
    subject: str | None
    mailer: str | None

    model_config = {"from_attributes": True}


class SentEmailDetail(BaseModel):
    id: uuid.UUID
    created_at: UtcDatetime | None
    mail_from: str | None
    to: str | None
    cc: str | None
    bcc: str | None
    subject: str | None
    body: str | None
    headers: str | None
    mailer: str | None

    model_config = {"from_attributes": True}


class EmailTemplateStats(BaseModel):
    template_key: str
    template_name: str
    source_location: str
    count: int
    last_sent: UtcDatetime | None


class EmailTemplatePreview(BaseModel):
    template_key: str
    template_name: str
    html: str


class ActivityLogItem(BaseModel):
    id: int
    created_at: UtcDatetime | None
    member_id: int | None
    member_name: str | None = None
    action_label: str
    request_method: str
    request_path: str
    response_status: int
    client_ip: str

    model_config = {"from_attributes": True}


class ActivityLogDetail(ActivityLogItem):
    request_input: str | None
    response_content: str | None = None
    client_user_agent: str | None = None


class ActivitySessionItem(BaseModel):
    member_id: int
    member_name: str
    started_at: UtcDatetime
    ended_at: UtcDatetime
    action_count: int
    actions: list[ActivityLogItem]


class ActivityStats(BaseModel):
    active_users_today: int
    total_actions_today: int
    actions_by_type: dict[str, int]


class TrackingConfigResponse(BaseModel):
    retention_months: int
