import re

from pydantic import BaseModel, field_validator

PERM_REGEX = re.compile(r"^[a-z]{3}_[a-z]{2}$")


# --- Responses ---


class PresignedUrlResponse(BaseModel):
    url: str


class StoreItemResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    extension: str
    mime_type: str
    size: int
    is_image: bool = False
    created_by: str | None = None
    created_at: str | None = None


class CommentResponse(BaseModel):
    id: int
    content: str
    author: str | None = None
    created_at: str | None = None


class DirShortResponse(BaseModel):
    type: str = "dir"
    id: int
    name: str
    description: str | None = None
    created_at: str | None = None
    deleted_at: str | None = None


class FileShortResponse(BaseModel):
    type: str = "file"
    id: int
    name: str | None = None
    extension: str | None = None
    description: str | None = None
    size: int = 0
    is_image: bool = False
    mime_type: str | None = None
    created_at: str | None = None
    deleted_at: str | None = None


class DirContentResponse(BaseModel):
    subdirs: dict[str, list[DirShortResponse]] = {}
    files: dict[str, list[FileShortResponse]] = {}


class PathEntry(BaseModel):
    id: int
    name: str


class PermissionsResponse(BaseModel):
    effective: list[str] = []
    own: list[str] = []
    parent: list[str] = []


class OrgRef(BaseModel):
    id: str
    label: str


class StateRef(BaseModel):
    id: str
    label: str


class SetsResponse(BaseModel):
    orgs: list[OrgRef] = []
    states: list[StateRef] = []


class DirDetailResponse(BaseModel):
    type: str = "dir"
    id: int
    name: str
    description: str | None = None
    path: list[PathEntry] = []
    permissions: PermissionsResponse = PermissionsResponse()
    recursive_permissions: bool = False
    content: DirContentResponse = DirContentResponse()
    sets: SetsResponse = SetsResponse()
    created_at: str | None = None
    updated_at: str | None = None
    deleted_at: str | None = None


class FileDetailResponse(BaseModel):
    type: str = "file"
    id: int
    archive_dir_id: int = 0
    name: str | None = None
    extension: str | None = None
    description: str | None = None
    size: int = 0
    is_image: bool = False
    mime_type: str | None = None
    path: list[PathEntry] = []
    active_version: StoreItemResponse | None = None
    comments: list[CommentResponse] = []
    trashed_comments: list[CommentResponse] = []
    created_at: str | None = None
    deleted_at: str | None = None


class UploadConfigResponse(BaseModel):
    extensions: list[str]
    minfilesize: int
    maxfilesize: int
    descminlength: int
    descmaxlength: int


# --- Requests ---


class DirSaveRequest(BaseModel):
    name: str
    description: str | None = None
    permissions: list[str] = []
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


class DirReceiveRequest(BaseModel):
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


class FileUpdateRequest(BaseModel):
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


class CommentCreateRequest(BaseModel):
    content: str

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 5 or len(v) > 1000:
            msg = "Kommentar muss 5-1000 Zeichen lang sein."
            raise ValueError(msg)
        return v
