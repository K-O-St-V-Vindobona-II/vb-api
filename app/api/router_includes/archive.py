from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.storage import StorageClient, get_storage
from app.db.database import get_db
from app.models.member import Member
from app.schemas.archive import (
    ArchiveDirDetailResponse,
    ArchiveFileDetailResponse,
    ArchiveSearchDirResult,
    ArchiveSearchFileResult,
    CommentCreateRequest,
    CommentCreateResponse,
    DirReceiveRequest,
    DirSaveRequest,
    FileUpdateRequest,
    PresignedUrlResponse,
    UnfiledUploadsResponse,
    UploadConfigResponse,
    UploadResponse,
)
from app.schemas.base import StatusIdResponse, StatusResponse
from app.services import archive_service

archive_router = APIRouter()


# --- Search ---


@archive_router.get("/search")
def search_archive(
    q: Annotated[str, Query(min_length=2)],
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> list[ArchiveSearchDirResult | ArchiveSearchFileResult]:
    """Search files and directories by name or description (min 2
    characters - lower than most other searches in this app since the
    archive has many meaningful 2-letter abbreviations, e.g. "BC"/"MC"/
    "FC"/"DC" committee protocol directories)."""
    results = archive_service.search_archive(db, user, q)
    return [
        ArchiveSearchDirResult.model_validate(r)
        if r["type"] == "dir"
        else ArchiveSearchFileResult.model_validate(r)
        for r in results
    ]


# --- Dirs ---


@archive_router.get("/dirs")
def get_root(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> ArchiveDirDetailResponse:
    """Return the root directory listing - only subdirectories/files the caller has
    insight permission into (everything, for archiveAdmin) - plus archive-wide
    aggregate stats (file/dir counts, total size, breakdown by extension)."""
    return ArchiveDirDetailResponse.model_validate(
        archive_service.get_root_content(db, user)
    )


@archive_router.get("/dirs/{dir_id}")
def get_dir(
    dir_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> ArchiveDirDetailResponse:
    """Return a directory by ID with its contents, path breadcrumbs, and
    effective permissions. Requires insight permission for the
    directory's org/state, or archiveAdmin."""
    return ArchiveDirDetailResponse.model_validate(
        archive_service.get_dir_detail(db, dir_id, user)
    )


@archive_router.post("/dirs", status_code=status.HTTP_201_CREATED)
def create_dir(
    data: DirSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> StatusIdResponse:
    """Create a new subdirectory within an existing directory (or the root, if parentId
    is omitted). Requires archiveAdmin."""
    d = archive_service.create_dir(db, data.model_dump(), user)
    return StatusIdResponse(status="ok", id=d.id)


@archive_router.put("/dirs/{dir_id}")
def update_dir(
    dir_id: int,
    data: DirSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> StatusResponse:
    """Update a directory's name, description, permissions, or recursive-permissions
    flag. Requires archiveAdmin."""
    archive_service.update_dir(db, dir_id, data.model_dump(), user)
    return StatusResponse(status="ok")


@archive_router.delete("/dirs/{dir_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dir(
    dir_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> None:
    """Soft-delete a directory (moves to trash) - hard-deletes instead if it's already
    empty. Requires archiveAdmin."""
    archive_service.delete_dir(db, dir_id, user)


@archive_router.patch("/dirs/{dir_id}/restore")
def restore_dir(
    dir_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> StatusResponse:
    """Restore a soft-deleted directory from trash. Requires archiveAdmin."""
    archive_service.restore_dir(db, dir_id, user)
    return StatusResponse(status="ok")


@archive_router.delete("/dirs/{dir_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_dir(
    dir_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> None:
    """Permanently delete an already-trashed, empty directory. 409 if it isn't currently
    in the trash or still has content. Irreversible. Requires archiveAdmin."""
    archive_service.purge_dir(db, dir_id, user)


@archive_router.post("/dirs/{dir_id}/receive")
def receive_in_dir(
    dir_id: int,
    data: DirReceiveRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> StatusResponse:
    """Move files or directories (clipboard paste) into this directory.
    Requires archiveAdmin."""
    archive_service.receive_items(db, dir_id, data.type, data.ids, user)
    return StatusResponse(status="ok")


@archive_router.post("/dirs/receive")
def receive_in_root(
    data: DirReceiveRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> StatusResponse:
    """Move files or directories (clipboard paste) into the root
    directory. Requires archiveAdmin."""
    archive_service.receive_items(db, 0, data.type, data.ids, user)
    return StatusResponse(status="ok")


# --- Files ---


@archive_router.get("/files/{file_id}")
def get_file(
    file_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> ArchiveFileDetailResponse:
    """Return file metadata including versions and comments. Requires insight permission
    for the file's directory, or archiveAdmin - admin-only for unfiled uploads, which
    have no directory to check permissions against."""
    return ArchiveFileDetailResponse.model_validate(
        archive_service.get_file_detail(db, file_id, user)
    )


@archive_router.put("/files/{file_id}")
def update_file(
    file_id: int,
    data: FileUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> StatusResponse:
    """Update a file's description. Requires archiveAdmin."""
    archive_service.update_file(db, file_id, data.model_dump(), user)
    return StatusResponse(status="ok")


@archive_router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(
    file_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> None:
    """Soft-delete a file (moves to trash). Requires archiveAdmin."""
    archive_service.delete_file(db, file_id, user)


@archive_router.patch("/files/{file_id}/restore")
def restore_file(
    file_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> StatusResponse:
    """Restore a soft-deleted file from trash. Requires archiveAdmin."""
    archive_service.restore_file(db, file_id, user)
    return StatusResponse(status="ok")


@archive_router.get("/files/{file_id}/url")
def file_url(
    file_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> PresignedUrlResponse:
    """Generate a time-limited presigned S3 URL for a file's original version. Requires
    insight permission for the file's directory, or archiveAdmin."""
    url = archive_service.get_presigned_url(
        db,
        file_id,
        user,
        storage,
    )
    return PresignedUrlResponse(url=url)


@archive_router.get("/files/{file_id}/url/{size}")
def file_thumb_url(
    file_id: int,
    size: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> PresignedUrlResponse:
    """Generate a time-limited presigned S3 URL for an image thumbnail at a given size.
    Requires insight permission for the file's directory, or archiveAdmin."""
    url = archive_service.get_presigned_url(
        db,
        file_id,
        user,
        storage,
        size,
    )
    return PresignedUrlResponse(url=url)


# --- Comments ---


@archive_router.post("/files/{file_id}/comments", status_code=status.HTTP_201_CREATED)
def create_comment(
    file_id: int,
    data: CommentCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> CommentCreateResponse:
    """Add a comment to a file. Requires insight permission for the file's directory, or
    archiveAdmin - admin-only for unfiled uploads."""
    comment = archive_service.create_comment(db, file_id, data.content, user)
    return CommentCreateResponse.model_validate({"status": "ok", "comment": comment})


@archive_router.delete(
    "/files/{file_id}/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_comment(
    file_id: int,
    comment_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> None:
    """Delete a comment from a file. The comment's own author or
    archiveAdmin may delete it."""
    archive_service.delete_comment(db, file_id, comment_id, user)


# --- Upload ---


@archive_router.get("/upload/config")
def get_upload_config(
    _user: Annotated[Member, Depends(get_current_user)],
) -> UploadConfigResponse:
    """Return upload constraints (allowed extensions, min/max file size, description
    length). No special permission - any authenticated member."""
    return UploadConfigResponse.model_validate(archive_service.get_upload_config())


@archive_router.get("/upload/unfiled")
def get_unfiled(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> UnfiledUploadsResponse:
    """List the caller's own uploaded files that have not yet been filed into a
    directory - not everyone's, just the current user's (see
    get_unsorted_upload_count() in archive_service.py for the org-wide total)."""
    return UnfiledUploadsResponse.model_validate(
        {"files": archive_service.get_unfiled_uploads(db, user.id)}
    )


@archive_router.post("/upload", status_code=status.HTTP_201_CREATED)
def upload(
    file: Annotated[UploadFile, File()],
    description: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> UploadResponse:
    """Upload a file to the archive. No special permission - any authenticated member
    may upload; the file lands unfiled (visible only to its uploader and
    archiveAdmin, see GET /upload/unfiled) until an admin files it into a directory."""
    result = archive_service.upload_file(
        db,
        file,
        description,
        user.id,
        storage,
    )
    return UploadResponse.model_validate({"status": "ok", "file": result})
