import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.schemas.base import UtcDatetime


class EnvironmentResponse(BaseModel):
    environment: str


class PermissionRuleResponse(BaseModel):
    permission: str
    description: str
    cns: list[str]


class PermissionRulesResponse(BaseModel):
    rules: list[PermissionRuleResponse]
    dev_superuser_cn: str | None


# Pydantic v2 serializes Decimal as a JSON string by default, not a
# number - silently breaks the frontend's `number` typing. Same fix as
# app/schemas/p4x.py's MoneyOut: force it back to a bare JSON number via
# a PlainSerializer.
_DurationSecondsOut = Annotated[
    Decimal, PlainSerializer(float, return_type=float, when_used="json")
]


class ScheduledJobRunSummary(BaseModel):
    exit_code: int
    output: str | None
    started_at: datetime
    finished_at: datetime
    duration_seconds: _DurationSecondsOut

    model_config = ConfigDict(from_attributes=True)


class ScheduledJobRunListItem(ScheduledJobRunSummary):
    id: uuid.UUID
    job_id: str


class TableColumn(BaseModel):
    name: str
    type: str
    nullable: bool
    primary_key: bool


class TableDataResponse(BaseModel):
    """Response shape for the generic SQL table browser (GET
    /tables/{table_name}). `rows` deliberately stays dict[str, object] -
    this endpoint reflects on arbitrary tables via SQLAlchemy inspection,
    so each row's actual keys/types differ per table; that's the point of
    the tool, not a typing gap to close."""

    table_name: str
    columns: list[TableColumn]
    rows: list[dict[str, object]]
    total: int
    page: int
    page_size: int


class ScheduledJobResponse(BaseModel):
    id: str
    name: str
    trigger: str
    next_run: str | None = None
    description: str | None = None
    last_run: ScheduledJobRunSummary | None = None


class BackupTriggerResponse(BaseModel):
    backup_name: str
    triggered_at: UtcDatetime
