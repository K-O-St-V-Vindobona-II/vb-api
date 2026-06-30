from pydantic import BaseModel, ConfigDict, EmailStr


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
    id: int

    cn: str = ""
    default_image: int | None = None
    permissions: list[str] = []
    google_linked: bool = False
    chroniclemail: bool = False
    session_idle_timeout: int = 30

    model_config = ConfigDict(from_attributes=True)
