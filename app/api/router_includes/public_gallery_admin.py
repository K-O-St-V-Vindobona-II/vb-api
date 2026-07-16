import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.api.auth_guards import require_permission
from app.core.storage import StorageClient, get_storage
from app.db.database import get_db
from app.models.member import Member
from app.models.public_gallery_image import PublicGalleryImage
from app.schemas.public_gallery import (
    GalleryImageAdminResponse,
    GalleryImageMoveRequest,
    GalleryImageUpdateRequest,
)
from app.services import public_gallery_service

# Every route below requires the "publicContentEditor" permission - this
# router manages content for the public site, not the public site itself
# (see public_site.py for the unauthenticated counterpart).
public_gallery_admin_router = APIRouter()

RequirePublicContentEditor = Annotated[
    Member, Depends(require_permission("publicContentEditor"))
]


def _to_admin_response(
    img: PublicGalleryImage,
    storage: StorageClient,
) -> GalleryImageAdminResponse:
    return GalleryImageAdminResponse(
        id=img.id,
        url=public_gallery_service.get_presigned_url(img, storage),
        caption=img.caption,
        sort_order=img.sort_order,
        is_published=img.is_published,
        width=img.width,
        height=img.height,
        size=img.size,
        created_at=img.created_at,
    )


@public_gallery_admin_router.get("/images")
def list_images(
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageClient, Depends(get_storage)],
    _current_user: RequirePublicContentEditor,
) -> list[GalleryImageAdminResponse]:
    """List all gallery images (published and unpublished), in display order."""
    images = public_gallery_service.list_admin_images(db)
    return [_to_admin_response(img, storage) for img in images]


@public_gallery_admin_router.post("/images")
def upload_image(
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    current_user: RequirePublicContentEditor,
    storage: Annotated[StorageClient, Depends(get_storage)],
    caption: Annotated[str | None, Form()] = None,
) -> GalleryImageAdminResponse:
    """Upload a new gallery image."""
    img = public_gallery_service.upload_image(
        db, file, caption, current_user.id, storage
    )
    return _to_admin_response(img, storage)


@public_gallery_admin_router.put("/images/{image_id}")
def update_image(
    image_id: uuid.UUID,
    data: GalleryImageUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageClient, Depends(get_storage)],
    _current_user: RequirePublicContentEditor,
) -> GalleryImageAdminResponse:
    """Update a gallery image's caption or publish state."""
    img = public_gallery_service.get_image_or_404(db, image_id)
    public_gallery_service.update_image(db, img, data.caption, data.is_published)
    return _to_admin_response(img, storage)


@public_gallery_admin_router.post("/images/{image_id}/move")
def move_image(
    image_id: uuid.UUID,
    data: GalleryImageMoveRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequirePublicContentEditor,
) -> dict[str, str]:
    """Move a gallery image up or down (swaps sort_order with its neighbor)."""
    img = public_gallery_service.get_image_or_404(db, image_id)
    public_gallery_service.move_image(db, img, data.direction)
    return {"status": "ok"}


@public_gallery_admin_router.delete("/images/{image_id}")
def delete_image(
    image_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageClient, Depends(get_storage)],
    _current_user: RequirePublicContentEditor,
) -> dict[str, str]:
    """Delete a gallery image."""
    img = public_gallery_service.get_image_or_404(db, image_id)
    public_gallery_service.delete_image(db, img, storage)
    return {"status": "ok"}
