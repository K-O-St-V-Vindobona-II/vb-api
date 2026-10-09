from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.schemas.base import StrictInputModel

MAX_CAPTION_LENGTH = 150
MAX_CONTACT_MESSAGE_LENGTH = 4000
MAX_CONTACT_NAME_LENGTH = 100
_FIRST_PRINTABLE = 0x20
_DELETE = 0x7F


class GalleryImagePublicResponse(BaseModel):
    id: UUID
    url: str
    caption: str | None = None
    width: int
    height: int

    model_config = ConfigDict(from_attributes=True)


class GalleryImageAdminResponse(BaseModel):
    id: UUID
    url: str
    caption: str | None = None
    sort_order: int
    is_published: bool
    width: int
    height: int
    size: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GalleryImageUpdateRequest(StrictInputModel):
    caption: str | None = None
    is_published: bool = True

    @field_validator("caption")
    @classmethod
    def caption_max_length(cls, v: str | None) -> str | None:
        if v and len(v) > MAX_CAPTION_LENGTH:
            msg = f"Maximal {MAX_CAPTION_LENGTH} Zeichen."
            raise ValueError(msg)
        return v


class ContactFormRequest(StrictInputModel):
    name: str = Field(min_length=1, max_length=MAX_CONTACT_NAME_LENGTH)
    email: EmailStr
    message: str = Field(min_length=1, max_length=MAX_CONTACT_MESSAGE_LENGTH)
    # Honeypot field: must stay empty. Real users never see or fill it
    # (hidden via CSS in the SPA); bots that blindly fill every field trip it.
    website: str = ""

    @field_validator("name")
    @classmethod
    def name_is_a_single_line(cls, v: str) -> str:
        """The name goes into the subject line of the notification mail."""
        if any(ord(char) < _FIRST_PRINTABLE or ord(char) == _DELETE for char in v):
            msg = "The name must not contain control characters."
            raise ValueError(msg)
        return v

    @field_validator("website")
    @classmethod
    def honeypot_must_be_empty(cls, v: str) -> str:
        if v:
            msg = "Invalid submission."
            raise ValueError(msg)
        return v
