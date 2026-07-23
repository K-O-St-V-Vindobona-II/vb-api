import re

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import StrictInputModel

PERM_REGEX = re.compile(r"^[a-z]{3}_[a-z]{2}$")


# --- Responses ---


class PresignedUrlResponse(BaseModel):
    url: str


# --- Requests ---


class DirSaveRequest(StrictInputModel):
    name: str
    description: str | None = None
    permissions: list[str] = Field(default_factory=list)
    recursive_permissions: bool = False
    parentId: int | None = None  # noqa: N815

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3 or len(v) > 64:
            msg = "Name muss 3-64 Zeichen lang sein."
            raise ValueError(msg)
        return v

    @field_validator("description")
    @classmethod
    def validate_description(
        cls,
        v: str | None,
    ) -> str | None:
        if v is not None and len(v) > 128:
            msg = "Beschreibung max. 128 Zeichen."
            raise ValueError(msg)
        return v

    @field_validator("permissions")
    @classmethod
    def validate_permissions(
        cls,
        v: list[str],
    ) -> list[str]:
        for p in v:
            if not PERM_REGEX.match(p):
                msg = f"Ungültiges Format: {p}"
                raise ValueError(msg)
        return v


class DirReceiveRequest(StrictInputModel):
    type: str
    ids: list[int]
    action: str = "move"

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ("dir", "file"):
            msg = "type muss 'dir' oder 'file' sein."
            raise ValueError(msg)
        return v

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v != "move":
            msg = "Nur 'move' wird unterstützt."
            raise ValueError(msg)
        return v


class FileUpdateRequest(StrictInputModel):
    description: str | None = None

    @field_validator("description")
    @classmethod
    def validate_description(
        cls,
        v: str | None,
    ) -> str | None:
        if v is not None and len(v) > 128:
            msg = "Beschreibung max. 128 Zeichen."
            raise ValueError(msg)
        return v


class CommentCreateRequest(StrictInputModel):
    content: str

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 5 or len(v) > 1000:
            msg = "Kommentar muss 5-1000 Zeichen lang sein."
            raise ValueError(msg)
        return v
