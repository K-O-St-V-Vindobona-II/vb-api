import hashlib
import io
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, UploadFile, status
from PIL import Image as PILImage
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.storage import S3_PATH_PUBLIC_GALLERY, StorageClient
from app.models.public_gallery_image import PublicGalleryImage

ALLOWED_TYPES = {"image/jpeg", "image/png"}
MAX_FILE_SIZE = 8 * 1024 * 1024


def _s3_key(sha256_hash: str) -> str:
    return f"{S3_PATH_PUBLIC_GALLERY}/{sha256_hash}"


def list_public_images(db: Session) -> list[PublicGalleryImage]:
    return (
        db.query(PublicGalleryImage)
        .filter(PublicGalleryImage.is_published.is_(True))
        .order_by(PublicGalleryImage.sort_order)
        .all()
    )


def list_admin_images(db: Session) -> list[PublicGalleryImage]:
    return db.query(PublicGalleryImage).order_by(PublicGalleryImage.sort_order).all()


def get_image_or_404(db: Session, image_id: uuid.UUID) -> PublicGalleryImage:
    img = db.get(PublicGalleryImage, image_id)
    if not img:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bild nicht gefunden.",
        )
    return img


def get_presigned_url(img: PublicGalleryImage, storage: StorageClient) -> str:
    return storage.generate_presigned_url(
        _s3_key(img.sha256_hash),
        expires_in=3600,
        filename=f"vindobona2-galerie-{img.id}.{img.extension}",
        content_type=img.content_type,
    )


def upload_image(
    db: Session,
    file: UploadFile,
    caption: str | None,
    created_by: int | None,
    storage: StorageClient,
) -> PublicGalleryImage:
    content = file.file.read()
    file_size = len(content)

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Datei zu groß (max. 8 MB).",
        )

    content_type = file.content_type or ""
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Nur JPEG- und PNG-Dateien erlaubt.",
        )

    try:
        pil_img = PILImage.open(io.BytesIO(content))
        width, height = pil_img.size
    except (OSError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Datei ist kein gültiges Bild.",
        ) from None

    sha256 = hashlib.sha256(content).hexdigest()
    duplicate = db.query(PublicGalleryImage).filter_by(sha256_hash=sha256).first()
    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Dieses Bild ist bereits in der Galerie vorhanden.",
        )

    key = _s3_key(sha256)
    if not storage.exists(key):
        storage.upload(key, content, content_type)

    ext = content_type.split("/")[-1]
    if ext == "jpeg":
        ext = "jpg"

    next_sort_order = (
        db.query(func.max(PublicGalleryImage.sort_order)).scalar() or 0
    ) + 1

    now = datetime.now(UTC)
    img = PublicGalleryImage(
        sha256_hash=sha256,
        extension=ext,
        content_type=content_type,
        size=file_size,
        width=width,
        height=height,
        caption=caption,
        sort_order=next_sort_order,
        is_published=True,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    db.add(img)
    db.commit()
    db.refresh(img)
    return img


def update_image(
    db: Session,
    img: PublicGalleryImage,
    caption: str | None,
    is_published: bool,  # noqa: FBT001
) -> None:
    img.caption = caption
    img.is_published = is_published
    img.updated_at = datetime.now(UTC)
    db.commit()


def move_image(db: Session, img: PublicGalleryImage, direction: str) -> None:
    comparator = PublicGalleryImage.sort_order < img.sort_order
    ordering = PublicGalleryImage.sort_order.desc()
    if direction == "down":
        comparator = PublicGalleryImage.sort_order > img.sort_order
        ordering = PublicGalleryImage.sort_order.asc()

    neighbor = (
        db.query(PublicGalleryImage).filter(comparator).order_by(ordering).first()
    )
    if not neighbor:
        return

    img.sort_order, neighbor.sort_order = neighbor.sort_order, img.sort_order
    db.commit()


def delete_image(db: Session, img: PublicGalleryImage, storage: StorageClient) -> None:
    # sha256_hash is unique per row (see upload_image()'s duplicate check), so
    # no other row can ever reference this S3 object - safe to remove both.
    storage.delete(_s3_key(img.sha256_hash))
    db.delete(img)
    db.commit()
