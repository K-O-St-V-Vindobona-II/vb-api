from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    UploadFile,
)
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.storage import StorageClient, get_storage
from app.db.database import get_db
from app.models.member import Member
from app.schemas.archive import (
    CommentCreateRequest,
    DirReceiveRequest,
    DirSaveRequest,
    FileUpdateRequest,
    PresignedUrlResponse,
)
from app.services import archive_service

archive_router = APIRouter()


# --- Search ---


@archive_router.get("/search")
def search_archive(
    q: Annotated[str, Query(min_length=3)],
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> list[dict[str, object]]:
    """Search files and directories by name or description (min 3 characters)."""
    return archive_service.search_archive(db, user, q)


# --- Dirs ---


@archive_router.get("/dirs")
def get_root(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, object]:
    """Return the root directory listing with subdirectories and files."""
    return archive_service.get_root_content(db, user)


@archive_router.get("/dirs/{dir_id}")
def get_dir(
    dir_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, object]:
    """Return a directory by ID with its contents, path breadcrumbs, and permissions."""
    return archive_service.get_dir_detail(db, dir_id, user)


@archive_router.post("/dirs")
def create_dir(
    data: DirSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, str | int]:
    """Create a new subdirectory within an existing directory."""
    d = archive_service.create_dir(db, data.model_dump(), user)
    return {"status": "ok", "id": d.id}


@archive_router.put("/dirs/{dir_id}")
def update_dir(
    dir_id: int,
    data: DirSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, str]:
    """Update a directory's name or description."""
    archive_service.update_dir(db, dir_id, data.model_dump(), user)
    return {"status": "ok"}


@archive_router.delete("/dirs/{dir_id}")
def delete_dir(
    dir_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, str]:
    """Soft-delete a directory (moves to trash)."""
    archive_service.delete_dir(db, dir_id, user)
    return {"status": "ok"}


@archive_router.patch("/dirs/{dir_id}/restore")
def restore_dir(
    dir_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, str]:
    """Restore a soft-deleted directory from trash."""
    archive_service.restore_dir(db, dir_id, user)
    return {"status": "ok"}


@archive_router.post("/dirs/{dir_id}/receive")
def receive_in_dir(
    dir_id: int,
    data: DirReceiveRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, str]:
    """Move files or directories into this directory (clipboard paste)."""
    archive_service.receive_items(db, dir_id, data.type, data.ids, user)
    return {"status": "ok"}


@archive_router.post("/dirs/receive")
def receive_in_root(
    data: DirReceiveRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, str]:
    """Move files or directories into the root directory."""
    archive_service.receive_items(db, 0, data.type, data.ids, user)
    return {"status": "ok"}


# --- Files ---


@archive_router.get("/files/{file_id}")
def get_file(
    file_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, object]:
    """Return file metadata including versions and comments."""
    return archive_service.get_file_detail(db, file_id, user)


@archive_router.put("/files/{file_id}")
def update_file(
    file_id: int,
    data: FileUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, str]:
    """Update a file's name, description, or directory assignment."""
    archive_service.update_file(db, file_id, data.model_dump(), user)
    return {"status": "ok"}


@archive_router.delete("/files/{file_id}")
def delete_file(
    file_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, str]:
    """Soft-delete a file (moves to trash)."""
    archive_service.delete_file(db, file_id, user)
    return {"status": "ok"}


@archive_router.patch("/files/{file_id}/restore")
def restore_file(
    file_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, str]:
    """Restore a soft-deleted file from trash."""
    archive_service.restore_file(db, file_id, user)
    return {"status": "ok"}


@archive_router.get("/files/{file_id}/download")
def download_file(
    file_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> Response:
    """Download the original file from S3 storage."""
    return archive_service.serve_download(
        db,
        file_id,
        user,
        storage,
    )


@archive_router.get("/files/{file_id}/download/{size}")
def download_file_thumb(
    file_id: int,
    size: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> Response:
    """Download a resized thumbnail of an image file."""
    return archive_service.serve_download(
        db,
        file_id,
        user,
        storage,
        size,
    )


@archive_router.get(
    "/files/{file_id}/url",
    response_model=PresignedUrlResponse,
)
def file_url(
    file_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> dict[str, str]:
    """Generate a presigned S3 URL for the original file."""
    url = archive_service.get_presigned_url(
        db,
        file_id,
        user,
        storage,
    )
    return {"url": url}


@archive_router.get(
    "/files/{file_id}/url/{size}",
    response_model=PresignedUrlResponse,
)
def file_thumb_url(
    file_id: int,
    size: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> dict[str, str]:
    """Generate a presigned S3 URL for an image thumbnail."""
    url = archive_service.get_presigned_url(
        db,
        file_id,
        user,
        storage,
        size,
    )
    return {"url": url}


# --- Comments ---


@archive_router.post("/files/{file_id}/comments")
def create_comment(
    file_id: int,
    data: CommentCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, object]:
    """Add a comment to a file."""
    comment = archive_service.create_comment(db, file_id, data.content, user.id)
    return {"status": "ok", "comment": comment}


@archive_router.delete("/files/{file_id}/comments/{comment_id}")
def delete_comment(
    file_id: int,
    comment_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, str]:
    """Delete a comment from a file."""
    archive_service.delete_comment(db, file_id, comment_id, user)
    return {"status": "ok"}


# --- Upload ---


@archive_router.get("/upload/config")
def get_upload_config(
    _user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, object]:
    """Return upload constraints (max file size, allowed extensions)."""
    return archive_service.get_upload_config()


@archive_router.get("/upload/unfiled")
def get_unfiled(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, object]:
    """List uploaded files that have not yet been assigned to a directory."""
    return {"files": archive_service.get_unfiled_uploads(db, user.id)}


@archive_router.post("/upload")
def upload(
    file: Annotated[UploadFile, File()],
    description: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> dict[str, object]:
    """Upload one or more files to the archive."""
    result = archive_service.upload_file(
        db,
        file,
        description,
        user.id,
        storage,
    )
    return {"status": "ok", "file": result}
