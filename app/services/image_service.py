import hashlib
import io
from datetime import UTC, datetime
from urllib.parse import quote

from botocore.exceptions import ClientError
from fastapi import HTTPException, UploadFile, status
from fastapi.responses import Response
from PIL import Image as PILImage
from sqlalchemy.orm import Session

from app.core.storage import (
    S3_PATH_STANDESDB_CACHE,
    S3_PATH_STANDESDB_IMAGES,
    THUMBNAIL_CACHE_VERSION,
    StorageClient,
    generate_thumbnail,
)
from app.models.standesdb_image import StandesdbImage

ALLOWED_TYPES = {"image/jpeg", "image/png"}
MAX_FILE_SIZE = 5 * 1024 * 1024

STANDESDB_THUMB_SIZE = 400


def get_image_record(
    db: Session,
    owner_type: str,
    owner_id: int,
    image_id: int,
) -> StandesdbImage:
    img = (
        db.query(StandesdbImage)
        .filter(
            StandesdbImage.id == image_id,
            StandesdbImage.owner_type == owner_type,
            StandesdbImage.owner_id == owner_id,
            StandesdbImage.deleted_at.is_(None),
        )
        .first()
    )
    if not img:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bild nicht gefunden.",
        )
    return img


def serve_download(
    img: StandesdbImage,
    storage: StorageClient,
) -> Response:
    key = f"{S3_PATH_STANDESDB_IMAGES}/{img.sha256_hash}"
    try:
        content = storage.download(key)
    except ClientError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Originaldatei nicht gefunden.",
        ) from None
    filename = f"{img.owner_type}_{img.owner_id}_{img.id}.{img.extension or 'jpg'}"
    safe = quote(filename, safe=".-_")
    return Response(
        content=content,
        media_type=img.type or "image/jpeg",
        headers={
            "Content-Disposition": (f"attachment; filename*=UTF-8''{safe}"),
        },
    )


def _thumbnail_cache_key(img: StandesdbImage) -> str:
    return f"{S3_PATH_STANDESDB_CACHE}/{img.sha256_hash}.{THUMBNAIL_CACHE_VERSION}"


def get_presigned_url(
    img: StandesdbImage,
    storage: StorageClient,
    *,
    thumb: bool = False,
) -> str:
    if thumb:
        _ensure_cache(img, storage)
        key = _thumbnail_cache_key(img)
    else:
        key = f"{S3_PATH_STANDESDB_IMAGES}/{img.sha256_hash}"
    filename = f"{img.owner_type}_{img.owner_id}_{img.id}.{img.extension or 'jpg'}"
    return storage.generate_presigned_url(
        key,
        filename=filename,
        content_type=img.type or "image/jpeg",
    )


def get_images_for_owner(
    db: Session,
    owner_type: str,
    owner_id: int,
) -> list[StandesdbImage]:
    return (
        db.query(StandesdbImage)
        .filter(
            StandesdbImage.owner_type == owner_type,
            StandesdbImage.owner_id == owner_id,
            StandesdbImage.deleted_at.is_(None),
        )
        .order_by(StandesdbImage.id)
        .all()
    )


def _ensure_cache(
    img: StandesdbImage,
    storage: StorageClient,
) -> None:
    cache_key = _thumbnail_cache_key(img)
    if storage.exists(cache_key):
        return

    original_key = f"{S3_PATH_STANDESDB_IMAGES}/{img.sha256_hash}"
    try:
        data = storage.download(original_key)
    except ClientError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Originaldatei nicht gefunden.",
        ) from None

    try:
        thumb_bytes, content_type = generate_thumbnail(
            data,
            STANDESDB_THUMB_SIZE,
            preserve_png=True,
            source_mime=img.type,
        )
    except (OSError, ValueError):
        return
    storage.upload(cache_key, thumb_bytes, content_type)


def upload_image(
    db: Session,
    owner_type: str,
    owner_id: int,
    file: UploadFile,
    description: str | None,
    created_by: int | None,
    storage: StorageClient,
) -> StandesdbImage:
    content = file.file.read()
    file_size = len(content)

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Datei zu groß (max. 5 MB).",
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

    key = f"{S3_PATH_STANDESDB_IMAGES}/{sha256}"
    if not storage.exists(key):
        storage.upload(key, content, content_type)

    ext = content_type.split("/")[-1]
    if ext == "jpeg":
        ext = "jpg"

    existing_count = (
        db.query(StandesdbImage)
        .filter(
            StandesdbImage.owner_type == owner_type,
            StandesdbImage.owner_id == owner_id,
            StandesdbImage.deleted_at.is_(None),
        )
        .count()
    )

    now = datetime.now(UTC)
    img = StandesdbImage(
        owner_type=owner_type,
        owner_id=owner_id,
        extension=ext,
        type=content_type,
        size=file_size,
        width=width,
        height=height,
        sha256_hash=sha256,
        description=description,
        default=1 if existing_count == 0 else 0,
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
    img: StandesdbImage,
    description: str | None,
    set_default: bool,  # noqa: FBT001
) -> None:
    img.description = description
    img.updated_at = datetime.now(UTC)

    if set_default:
        db.query(StandesdbImage).filter(
            StandesdbImage.owner_type == img.owner_type,
            StandesdbImage.owner_id == img.owner_id,
            StandesdbImage.deleted_at.is_(None),
        ).update({"default": 0})
        img.default = 1

    db.commit()


def delete_image(
    db: Session,
    img: StandesdbImage,
) -> None:
    # Intentional: soft-delete only. The S3 object (keyed by sha256_hash)
    # is never removed, even after hard-deletion of the owning record.
    img.deleted_at = datetime.now(UTC)
    db.commit()
