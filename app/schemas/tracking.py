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


class TrackingConfigResponse(BaseModel):
    retention_months: int
