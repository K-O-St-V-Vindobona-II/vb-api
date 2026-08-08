import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import StrictInputModel

PERM_REGEX = re.compile(r"^[a-z]{3}_[a-z]{2}$")


# --- Responses ---


class PresignedUrlResponse(BaseModel):
    url: str


class ArchiveDirShort(BaseModel):
    """One directory row as it appears inside a dir listing's `content`
    bucket - see _dir_short() in archive_service.py."""

    type: Literal["dir"] = "dir"
    id: int
    name: str
    description: str | None
    created_at: str | None
    deleted_at: str | None


class ArchiveFileShort(BaseModel):
    """One file row as it appears inside a dir listing's `content` bucket
    or the unfiled-uploads/upload-result responses - see _file_short() in
    archive_service.py."""

    type: Literal["file"] = "file"
    id: int
    name: str
    extension: str
    description: str | None
    size: int
    is_image: bool
    mime_type: str
    created_at: str | None
    deleted_at: str | None


class ArchiveDirBucket(BaseModel):
    """Subdirectories split by how the caller may see them: `insight` (can
    browse into it), `admin` (visible only because the caller is
    archiveAdmin, no insight permission), `trashed` (archiveAdmin-only,
    soft-deleted)."""

    insight: list[ArchiveDirShort]
    admin: list[ArchiveDirShort]
    trashed: list[ArchiveDirShort]


class ArchiveFileBucket(BaseModel):
    """Same insight/admin/trashed split as ArchiveDirBucket, for files."""

    insight: list[ArchiveFileShort]
    admin: list[ArchiveFileShort]
    trashed: list[ArchiveFileShort]


class ArchiveDirContent(BaseModel):
    subdirs: ArchiveDirBucket
    files: ArchiveFileBucket


class ArchivePathEntry(BaseModel):
    id: int
    name: str


class ArchivePermissionsBlock(BaseModel):
    """Permission keys in `org_id_state_id` form (e.g. "vbw_up"). `own`
    and `parent` are always empty for a non-admin caller - only
    archiveAdmin sees the raw permission breakdown, everyone else only
    gets the merged `effective` list."""

    effective: list[str]
    own: list[str]
    parent: list[str]


class ArchiveOrgOption(BaseModel):
    id: str
    label: str


class ArchiveStateOption(BaseModel):
    id: str
    label: str


class ArchiveSets(BaseModel):
    """Reference data for the permission editor - empty for a non-admin
    caller on GET /dirs/{id} (see get_dir_detail()), always populated on
    the root listing (GET /dirs)."""

    orgs: list[ArchiveOrgOption]
    states: list[ArchiveStateOption]


class ArchiveExtensionStat(BaseModel):
    extension: str | None
    count: int
    size: int


class ArchiveStats(BaseModel):
    file_count: int
    unique_object_count: int
    dir_count: int
    total_size: int
    by_extension: list[ArchiveExtensionStat]


class ArchiveDirDetailResponse(BaseModel):
    """Shared response shape for both GET /dirs (the synthetic root, id=0)
    and GET /dirs/{dir_id} - see get_root_content()/get_dir_detail() in
    archive_service.py, which build the identical field set. `stats` is
    only ever populated on the root (permissions are meaningless there,
    so the info card shows archive-wide numbers instead); real
    directories always have stats=None."""

    type: Literal["dir"] = "dir"
    id: int
    name: str
    description: str | None
    path: list[ArchivePathEntry]
    permissions: ArchivePermissionsBlock
    recursive_permissions: bool
    content: ArchiveDirContent
    sets: ArchiveSets
    stats: ArchiveStats | None
    created_at: str | None
    updated_at: str | None
    deleted_at: str | None


class ArchiveStoreItemResponse(BaseModel):
    """The currently-active version of a file's content-addressed store
    item - see _store_item_response() in archive_service.py."""

    id: int
    name: str
    description: str | None
    extension: str
    mime_type: str
    size: int
    is_image: bool
    created_by: str | None
    created_at: str | None


class ArchiveCommentResponse(BaseModel):
    id: int
    content: str
    author: str | None
    created_at: str | None


class ArchiveFileDetailResponse(BaseModel):
    """Response shape for GET /files/{file_id} - see get_file_detail() in
    archive_service.py. `trashed_comments` is always empty for a
    non-admin caller."""

    type: Literal["file"] = "file"
    id: int
    archive_dir_id: int
    name: str
    extension: str
    description: str | None
    size: int
    is_image: bool
    mime_type: str
    path: list[ArchivePathEntry]
    active_version: ArchiveStoreItemResponse
    comments: list[ArchiveCommentResponse]
    trashed_comments: list[ArchiveCommentResponse]
    created_at: str | None
    deleted_at: str | None


class ArchiveSearchDirResult(BaseModel):
    type: Literal["dir"] = "dir"
    id: int
    name: str
    description: str | None
    path: str


class ArchiveSearchFileResult(BaseModel):
    type: Literal["file"] = "file"
    id: int
    name: str
    description: str | None
    extension: str
    is_image: bool
    path: str


class UploadConfigResponse(BaseModel):
    extensions: list[str]
    minfilesize: int
    maxfilesize: int
    descminlength: int
    descmaxlength: int


class UnfiledUploadsResponse(BaseModel):
    """Response shape for GET /upload/unfiled - the caller's own
    not-yet-filed uploads, see get_unfiled_uploads() in
    archive_service.py."""

    files: list[ArchiveFileShort]


class UploadResponse(BaseModel):
    status: str
    file: ArchiveFileShort


class CommentCreateResponse(BaseModel):
    status: str
    comment: ArchiveCommentResponse


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
