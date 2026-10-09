import uuid

from pydantic import BaseModel, EmailStr


# 1. Base Schema: Fields that are needed everywhere
class MemberBase(BaseModel):
    email: EmailStr | None = None
    vorname: str | None = None
    nachname: str | None = None
    couleurname: str | None = None
    org_id: str
    auth_locked: bool


# 2. Response Schema: How the Member leaves the backend
# towards the Vue frontend!
class MemberResponse(MemberBase):
    id: uuid.UUID

    cn: str
    default_image: uuid.UUID | None
    permissions: list[str]
    google_linked: bool
    chroniclemail: bool
    is_fee_member: bool
    session_idle_timeout: int


class ChroniclemailToggleResponse(BaseModel):
    chroniclemail: bool
