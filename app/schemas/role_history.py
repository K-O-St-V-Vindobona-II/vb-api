import uuid
from datetime import date

from pydantic import BaseModel

from app.models.enums import RoleAssignmentAction
from app.schemas.base import UtcDatetime


class RoleAssignmentEventResponse(BaseModel):
    """One entry of the role history, with the permissions the role confers to
    a member of that member's organisation (derived from the current rules)."""

    id: uuid.UUID
    occurred_at: UtcDatetime
    action: RoleAssignmentAction
    member_id: uuid.UUID
    member_cn: str
    actor_cn: str | None
    role_id: str
    role_label: str
    startdate: date
    enddate: date | None
    previous_enddate: date | None
    permissions: list[str]
