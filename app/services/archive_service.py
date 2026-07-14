import hashlib
from datetime import UTC, datetime
from urllib.parse import quote

from botocore.exceptions import ClientError
from fastapi import HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.storage import (
    S3_PATH_ARCHIVE_CACHE,
    S3_PATH_ARCHIVE_STORE,
    THUMBNAIL_CACHE_VERSION,
    StorageClient,
    generate_thumbnail,
)
from app.models.archive_dir import ArchiveDir
from app.models.archive_file import ArchiveFile
from app.models.archive_file_comment import (
    ArchiveFileComment,
)
from app.models.archive_file_version import (
    ArchiveFileVersion,
)
from app.models.archive_permission import (
    ArchivePermission,
)
from app.models.archive_store_item import (
    ArchiveStoreItem,
)
from app.models.member import Member
from app.models.org import Org
from app.models.state import State
from app.services.permission_service import (
    calculate_permissions,
)

THUMB_SIZES = {"xs": 16, "sm": 128, "md": 256, "lg": 550}

UPLOAD_EXTENSIONS = [
    "gif",
    "jpg",
    "jpeg",
    "png",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "pdf",
    "txt",
    "mp3",
    "mp4",
    "avi",
    "mpga",
    "ai",
    "eps",
]
UPLOAD_MIN_KB = 2
UPLOAD_MAX_KB = 6144
UPLOAD_DESC_MIN = 5
UPLOAD_DESC_MAX = 125


def _now() -> datetime:
    return datetime.now(UTC)


def _ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


# --- Permissions ---


def is_archive_admin(user: Member) -> bool:
    return "archiveAdmin" in calculate_permissions(user)


def get_effective_permissions(
    db: Session,
    dir_obj: ArchiveDir,
) -> list[str]:
    own = _own_permissions(db, dir_obj)
    inherited = _inherited_permissions(db, dir_obj)
    merged = list(set(own) | set(inherited))
    merged.sort()
    return merged


def _own_permissions(
    db: Session,
    dir_obj: ArchiveDir,
) -> list[str]:
    orgs = {o.id for o in db.query(Org).all()}
    states = {s.id for s in db.query(State).all()}
    result = []
    # Skip permissions referencing deleted orgs/states
    for p in dir_obj.archive_permissions:
        if p.org_id in orgs and p.state_id in states:
            key = f"{p.org_id}_{p.state_id}"
            if key not in result:
                result.append(key)
    return result


def _inherited_permissions(
    db: Session,
    dir_obj: ArchiveDir,
) -> list[str]:
    parent = (
        db.get(ArchiveDir, dir_obj.archive_dir_id) if dir_obj.archive_dir_id else None
    )
    if not parent:
        return []
    if parent.recursive_permissions:
        return get_effective_permissions(db, parent)
    return _inherited_permissions(db, parent)


def can_insight(
    user: Member,
    db: Session,
    dir_obj: ArchiveDir,
) -> bool:
    key = f"{user.org_id}_{user.state_id}"
    return key in get_effective_permissions(db, dir_obj)


def _require_admin(user: Member) -> None:
    if not is_archive_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Berechtigung: archiveAdmin",
        )


def _require_insight_or_admin(
    user: Member,
    db: Session,
    dir_obj: ArchiveDir,
) -> None:
    if is_archive_admin(user):
        return
    if not can_insight(user, db, dir_obj):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Keine Berechtigung für dieses Verzeichnis.",
        )


# --- File helpers ---


def _active_store_item(
    file_obj: ArchiveFile,
) -> ArchiveStoreItem | None:
    for fv in file_obj.file_versions:
        if fv.active and fv.store_item:
            return fv.store_item
    # Fall back to first version if active flag is missing (legacy data)
    if file_obj.file_versions:
        return file_obj.file_versions[0].store_item
    return None


def _file_short(
    file_obj: ArchiveFile,
) -> dict[str, object]:
    item = _active_store_item(file_obj)
    return {
        "type": "file",
        "id": file_obj.id,
        "name": item.name if item else None,
        "extension": item.extension if item else None,
        "description": file_obj.description,
        "size": item.size if item else 0,
        "is_image": item.is_image if item else False,
        "mime_type": item.mime_type if item else None,
        "created_at": _ts(item.created_at if item else None),
        "deleted_at": _ts(file_obj.deleted_at),
    }


def _dir_short(d: ArchiveDir) -> dict[str, object]:
    return {
        "type": "dir",
        "id": d.id,
        "name": d.name,
        "description": d.description,
        "created_at": _ts(d.created_at),
        "deleted_at": _ts(d.deleted_at),
    }


def _build_path(
    db: Session,
    dir_obj: ArchiveDir,
) -> list[dict[str, object]]:
    path = []
    current = dir_obj
    while current:
        path.append({"id": current.id, "name": current.name})
        current = (
            db.get(ArchiveDir, current.archive_dir_id)
            if current.archive_dir_id
            else None
        )
    path.reverse()
    return path


def _store_item_response(
    item: ArchiveStoreItem,
) -> dict[str, object]:
    return {
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "extension": item.extension,
        "mime_type": item.mime_type,
        "size": item.size,
        "is_image": item.is_image,
        "created_by": (item.member.cn if item.member else None),
        "created_at": _ts(item.created_at),
    }


def _comment_response(
    c: ArchiveFileComment,
) -> dict[str, object]:
    return {
        "id": c.id,
        "content": c.content,
        "author": c.member.cn if c.member else None,
        "created_at": _ts(c.created_at),
    }


# --- Dir operations ---


def _empty_content() -> dict[str, dict[str, list[dict[str, object]]]]:
    return {
        "subdirs": {"insight": [], "admin": [], "trashed": []},
        "files": {"insight": [], "admin": [], "trashed": []},
    }


def _classify_dir(
    d: ArchiveDir,
    admin: bool,  # noqa: FBT001
    user: Member,
    db: Session,
    bucket: dict[str, list[dict[str, object]]],
) -> None:
    if d.deleted_at:
        if admin:
            bucket["trashed"].append(_dir_short(d))
        return
    if can_insight(user, db, d):
        bucket["insight"].append(_dir_short(d))
    elif admin:
        bucket["admin"].append(_dir_short(d))


def _classify_file_root(
    f: ArchiveFile,
    admin: bool,  # noqa: FBT001
    bucket: dict[str, list[dict[str, object]]],
) -> None:
    if f.deleted_at:
        if admin:
            bucket["trashed"].append(_file_short(f))
        return
    if admin:
        bucket["admin"].append(_file_short(f))


def get_root_content(
    db: Session,
    user: Member,
) -> dict[str, object]:
    admin = is_archive_admin(user)
    content = _empty_content()

    dirs = (
        db.query(ArchiveDir)
        .filter(ArchiveDir.archive_dir_id == 0)
        .order_by(ArchiveDir.name)
        .all()
    )
    for d in dirs:
        _classify_dir(d, admin, user, db, content["subdirs"])

    files = db.query(ArchiveFile).filter(ArchiveFile.archive_dir_id == 0).all()
    for f in files:
        _classify_file_root(f, admin, content["files"])

    return {
        "type": "dir",
        "id": 0,
        "name": "Archiv",
        "description": None,
        "path": [],
        "permissions": {
            "effective": [],
            "own": [],
            "parent": [],
        },
        "recursive_permissions": False,
        "content": content,
        "sets": _get_sets(db),
        "created_at": None,
        "updated_at": None,
        "deleted_at": None,
    }


def _classify_file_in_dir(
    f: ArchiveFile,
    admin: bool,  # noqa: FBT001
    has_insight: bool,  # noqa: FBT001
    bucket: dict[str, list[dict[str, object]]],
) -> None:
    if f.deleted_at:
        if admin:
            bucket["trashed"].append(_file_short(f))
        return
    if has_insight:
        bucket["insight"].append(_file_short(f))
    elif admin:
        bucket["admin"].append(_file_short(f))


def _build_dir_detail_content(
    db: Session,
    dir_obj: ArchiveDir,
    user: Member,
    admin: bool,  # noqa: FBT001
) -> dict[str, dict[str, list[dict[str, object]]]]:
    content = _empty_content()

    children = dir_obj.children.order_by(ArchiveDir.name).all()
    for d in children:
        _classify_dir(d, admin, user, db, content["subdirs"])

    has_insight = can_insight(user, db, dir_obj)
    files = dir_obj.archive_files.all()
    for f in files:
        _classify_file_in_dir(f, admin, has_insight, content["files"])

    return content


def get_dir_detail(
    db: Session,
    dir_id: int,
    user: Member,
) -> dict[str, object]:
    dir_obj = db.get(ArchiveDir, dir_id)
    if not dir_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Verzeichnis nicht gefunden.",
        )
    _require_insight_or_admin(user, db, dir_obj)

    admin = is_archive_admin(user)
    content = _build_dir_detail_content(db, dir_obj, user, admin)

    own = _own_permissions(db, dir_obj)
    inherited = _inherited_permissions(db, dir_obj)
    effective = list(set(own) | set(inherited))
    effective.sort()

    return {
        "type": "dir",
        "id": dir_obj.id,
        "name": dir_obj.name,
        "description": dir_obj.description,
        "path": _build_path(db, dir_obj),
        "permissions": {
            "effective": effective,
            "own": own if admin else [],
            "parent": inherited if admin else [],
        },
        "recursive_permissions": bool(dir_obj.recursive_permissions),
        "content": content,
        "sets": _get_sets(db)
        if admin
        else {
            "orgs": [],
            "states": [],
        },
        "created_at": _ts(dir_obj.created_at),
        "updated_at": _ts(dir_obj.updated_at),
        "deleted_at": _ts(dir_obj.deleted_at),
    }


def create_dir(
    db: Session,
    data: dict[str, object],
    user: Member,
) -> ArchiveDir:
    _require_admin(user)
    parent_id = data.get("parentId") or 0
    if parent_id:
        parent = db.get(ArchiveDir, parent_id)
        if not parent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Übergeordnetes Verzeichnis nicht gefunden.",
            )

    now = _now()
    desc_raw = data.get("description")
    perms_raw = data.get("permissions", [])
    perms = list(perms_raw) if isinstance(perms_raw, list) else []
    dir_obj = ArchiveDir(
        name=str(data["name"]),
        description=str(desc_raw) if desc_raw is not None else None,
        archive_dir_id=parent_id,
        recursive_permissions=bool(data.get("recursive_permissions", False)),
        created_at=now,
        updated_at=now,
    )
    db.add(dir_obj)
    db.flush()
    _sync_permissions(db, dir_obj, [str(p) for p in perms])
    db.commit()
    return dir_obj


def update_dir(
    db: Session,
    dir_id: int,
    data: dict[str, object],
    user: Member,
) -> ArchiveDir:
    _require_admin(user)
    dir_obj = db.get(ArchiveDir, dir_id)
    if not dir_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Verzeichnis nicht gefunden.",
        )
    dir_obj.name = str(data["name"])
    desc_raw = data.get("description")
    dir_obj.description = str(desc_raw) if desc_raw is not None else None
    dir_obj.recursive_permissions = bool(data.get("recursive_permissions", False))
    dir_obj.updated_at = _now()
    perms_raw = data.get("permissions", [])
    perms = list(perms_raw) if isinstance(perms_raw, list) else []
    _sync_permissions(db, dir_obj, [str(p) for p in perms])
    db.commit()
    return dir_obj


def delete_dir(
    db: Session,
    dir_id: int,
    user: Member,
) -> None:
    _require_admin(user)
    dir_obj = db.get(ArchiveDir, dir_id)
    if not dir_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Verzeichnis nicht gefunden.",
        )

    has_children = (
        db.query(ArchiveDir).filter(ArchiveDir.archive_dir_id == dir_obj.id).count() > 0
    )
    has_files = (
        db.query(ArchiveFile).filter(ArchiveFile.archive_dir_id == dir_obj.id).count()
        > 0
    )
    if not has_children and not has_files:
        db.delete(dir_obj)
    else:
        # Intentional: only the DB row is soft-deleted. The S3 object is never
        # removed — files in the content-addressed store are kept indefinitely.
        dir_obj.deleted_at = _now()
    db.commit()


def restore_dir(
    db: Session,
    dir_id: int,
    user: Member,
) -> None:
    _require_admin(user)
    dir_obj = db.get(ArchiveDir, dir_id)
    if not dir_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Verzeichnis nicht gefunden.",
        )
    dir_obj.deleted_at = None
    dir_obj.updated_at = _now()
    db.commit()


def _validate_target_dir(db: Session, target_dir_id: int) -> None:
    if not target_dir_id:
        return
    target = db.get(ArchiveDir, target_dir_id)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zielverzeichnis nicht gefunden.",
        )


def _move_dir_item(
    db: Session,
    did: int,
    target_dir_id: int,
) -> None:
    d = db.get(ArchiveDir, did)
    if not d:
        return
    if did == target_dir_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Verzeichnis kann nicht in sich selbst verschoben werden.",
        )
    if target_dir_id and _is_descendant(db, target_dir_id, did):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Verzeichnis kann nicht in eigenes Unterverzeichnis verschoben werden."
            ),
        )
    d.archive_dir_id = target_dir_id
    d.updated_at = _now()


def _move_file_items(
    db: Session,
    item_ids: list[int],
    target_dir_id: int,
) -> None:
    for fid in item_ids:
        f = db.get(ArchiveFile, fid)
        if not f:
            continue
        f.archive_dir_id = target_dir_id


def receive_items(
    db: Session,
    target_dir_id: int,
    item_type: str,
    item_ids: list[int],
    user: Member,
) -> None:
    _require_admin(user)
    _validate_target_dir(db, target_dir_id)

    if item_type == "dir":
        for did in item_ids:
            _move_dir_item(db, did, target_dir_id)
    elif item_type == "file":
        _move_file_items(db, item_ids, target_dir_id)

    db.commit()


def _is_descendant(
    db: Session,
    candidate_id: int,
    ancestor_id: int,
) -> bool:
    current_id = candidate_id
    visited: set[int] = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        d = db.get(ArchiveDir, current_id)
        if not d:
            return False
        if d.archive_dir_id == ancestor_id:
            return True
        current_id = d.archive_dir_id
    return False


def _sync_permissions(
    db: Session,
    dir_obj: ArchiveDir,
    perms: list[str],
) -> None:
    db.query(ArchivePermission).filter(
        ArchivePermission.archive_dir_id == dir_obj.id
    ).delete()
    for p in perms:
        parts = p.split("_", 1)
        if len(parts) == 2:
            db.add(
                ArchivePermission(
                    archive_dir_id=dir_obj.id,
                    org_id=parts[0],
                    state_id=parts[1],
                )
            )
    db.flush()


def _get_sets(db: Session) -> dict[str, object]:
    return {
        "orgs": [
            {"id": o.id, "label": o.label}
            for o in db.query(Org).order_by(Org.order).all()
        ],
        "states": [
            {"id": s.id, "label": s.label}
            for s in db.query(State).order_by(State.order).all()
        ],
    }


# --- File operations ---


def get_file_detail(
    db: Session,
    file_id: int,
    user: Member,
) -> dict[str, object]:
    file_obj = db.get(ArchiveFile, file_id)
    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datei nicht gefunden.",
        )

    if file_obj.archive_dir_id:
        dir_obj = db.get(ArchiveDir, file_obj.archive_dir_id)
        if dir_obj:
            _require_insight_or_admin(user, db, dir_obj)

    admin = is_archive_admin(user)
    item = _active_store_item(file_obj)
    path = []
    if file_obj.archive_dir_id:
        dir_obj = db.get(ArchiveDir, file_obj.archive_dir_id)
        if dir_obj:
            path = _build_path(db, dir_obj)

    comments = (
        file_obj.comments.filter(ArchiveFileComment.deleted_at.is_(None))
        .order_by(ArchiveFileComment.created_at)
        .all()
    )
    trashed_comments = []
    if admin:
        trashed_comments = (
            file_obj.comments.filter(ArchiveFileComment.deleted_at.isnot(None))
            .order_by(ArchiveFileComment.created_at)
            .all()
        )

    return {
        "type": "file",
        "id": file_obj.id,
        "archive_dir_id": file_obj.archive_dir_id or 0,
        "name": item.name if item else None,
        "extension": item.extension if item else None,
        "description": file_obj.description,
        "size": item.size if item else 0,
        "is_image": item.is_image if item else False,
        "mime_type": item.mime_type if item else None,
        "path": path,
        "active_version": (_store_item_response(item) if item else None),
        "comments": [_comment_response(c) for c in comments],
        "trashed_comments": [_comment_response(c) for c in trashed_comments],
        "created_at": _ts(item.created_at if item else None),
        "deleted_at": _ts(file_obj.deleted_at),
    }


def update_file(
    db: Session,
    file_id: int,
    data: dict[str, object],
    user: Member,
) -> None:
    _require_admin(user)
    file_obj = db.get(ArchiveFile, file_id)
    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datei nicht gefunden.",
        )
    desc_raw = data.get("description")
    file_obj.description = str(desc_raw) if desc_raw is not None else None
    db.commit()


def delete_file(
    db: Session,
    file_id: int,
    user: Member,
) -> None:
    _require_admin(user)
    file_obj = db.get(ArchiveFile, file_id)
    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datei nicht gefunden.",
        )
    # Intentional: only the DB row is soft-deleted. The S3 object is never
    # removed — files in the content-addressed store are kept indefinitely.
    file_obj.deleted_at = _now()
    db.commit()


def restore_file(
    db: Session,
    file_id: int,
    user: Member,
) -> None:
    _require_admin(user)
    file_obj = db.get(ArchiveFile, file_id)
    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datei nicht gefunden.",
        )
    file_obj.deleted_at = None
    db.commit()


def _resolve_file_and_item(
    db: Session,
    file_id: int,
    user: Member,
) -> tuple[ArchiveFile, ArchiveStoreItem]:
    file_obj = db.get(ArchiveFile, file_id)
    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datei nicht gefunden.",
        )

    if file_obj.archive_dir_id:
        dir_obj = db.get(ArchiveDir, file_obj.archive_dir_id)
        if dir_obj:
            _require_insight_or_admin(user, db, dir_obj)

    item = _active_store_item(file_obj)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keine Version gefunden.",
        )
    return file_obj, item


def _serve_thumbnail(
    item: ArchiveStoreItem,
    size: str,
    storage: StorageClient,
) -> Response | None:
    thumb_data = _get_or_create_thumbnail(item, size, storage)
    if thumb_data is None:
        return None
    return Response(content=thumb_data, media_type="image/jpeg")


def serve_download(
    db: Session,
    file_id: int,
    user: Member,
    storage: StorageClient,
    size: str | None = None,
) -> Response:
    _, item = _resolve_file_and_item(db, file_id, user)

    if size and size in THUMB_SIZES and item.is_image:
        thumb_response = _serve_thumbnail(item, size, storage)
        if thumb_response is not None:
            return thumb_response

    key = f"{S3_PATH_ARCHIVE_STORE}/{item.sha256_hash}"
    try:
        data = storage.download(key)
    except ClientError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datei nicht im Speicher gefunden.",
        ) from None

    filename = f"{item.name}.{item.extension}"
    safe = quote(filename, safe=".-_")
    return Response(
        content=data,
        media_type=item.mime_type,
        headers={
            "Content-Disposition": (f"attachment; filename*=UTF-8''{safe}"),
        },
    )


def get_presigned_url(
    db: Session,
    file_id: int,
    user: Member,
    storage: StorageClient,
    size: str | None = None,
) -> str:
    file_obj = db.get(ArchiveFile, file_id)
    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datei nicht gefunden.",
        )

    if file_obj.archive_dir_id:
        dir_obj = db.get(ArchiveDir, file_obj.archive_dir_id)
        if dir_obj:
            _require_insight_or_admin(user, db, dir_obj)

    item = _active_store_item(file_obj)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keine Version gefunden.",
        )

    if size and size in THUMB_SIZES and item.is_image:
        _get_or_create_thumbnail(item, size, storage)
        return storage.generate_presigned_url(
            _thumbnail_cache_key(item, size),
            content_type="image/jpeg",
        )

    key = f"{S3_PATH_ARCHIVE_STORE}/{item.sha256_hash}"
    filename = f"{item.name}.{item.extension}"
    return storage.generate_presigned_url(
        key,
        filename=filename,
        content_type=item.mime_type,
    )


def _thumbnail_cache_key(item: ArchiveStoreItem, size: str) -> str:
    return (
        f"{S3_PATH_ARCHIVE_CACHE}/{item.sha256_hash}."
        f"{THUMBNAIL_CACHE_VERSION}.thumb_{size}"
    )


def _get_or_create_thumbnail(
    item: ArchiveStoreItem,
    size: str,
    storage: StorageClient,
) -> bytes | None:
    cache_key = _thumbnail_cache_key(item, size)
    if storage.exists(cache_key):
        return storage.download(cache_key)

    source_key = f"{S3_PATH_ARCHIVE_STORE}/{item.sha256_hash}"
    if not storage.exists(source_key):
        return None

    try:
        data = storage.download(source_key)
        thumb_bytes, content_type = generate_thumbnail(
            data,
            THUMB_SIZES[size],
        )
        storage.upload(cache_key, thumb_bytes, content_type)
    except (OSError, ValueError):
        return None
    else:
        return thumb_bytes


# --- Upload ---


def get_upload_config() -> dict[str, object]:
    return {
        "extensions": UPLOAD_EXTENSIONS,
        "minfilesize": UPLOAD_MIN_KB,
        "maxfilesize": UPLOAD_MAX_KB,
        "descminlength": UPLOAD_DESC_MIN,
        "descmaxlength": UPLOAD_DESC_MAX,
    }


def get_unfiled_uploads(
    db: Session,
    user_id: int,
) -> list[dict[str, object]]:
    versions = (
        db.query(ArchiveFileVersion)
        .join(ArchiveFile)
        .join(
            ArchiveStoreItem,
            ArchiveFileVersion.archive_store_item_id == ArchiveStoreItem.id,
        )
        .filter(
            ArchiveFile.archive_dir_id == 0,
            ArchiveFileVersion.active == True,  # noqa: E712
            ArchiveStoreItem.created_by == user_id,
        )
        .all()
    )
    return [_file_short(fv.archive_file) for fv in versions if fv.archive_file]


def upload_file(
    db: Session,
    file: UploadFile,
    description: str,
    user_id: int,
    storage: StorageClient,
) -> dict[str, object]:
    if not file.filename or "." not in file.filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Datei hat keine Dateiendung.",
        )

    parts = file.filename.rsplit(".", 1)
    name = parts[0]
    ext = parts[1].lower()

    if ext not in UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unerlaubtes Dateiformat: {ext}",
        )

    content = file.file.read()
    size = len(content)

    if size < UPLOAD_MIN_KB * 1024:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Datei zu klein. Minimum: {UPLOAD_MIN_KB} KB",
        )
    if size > UPLOAD_MAX_KB * 1024:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Datei zu groß. Maximum: {UPLOAD_MAX_KB} KB",
        )

    sha256 = hashlib.sha256(content).hexdigest()

    existing = (
        db.query(ArchiveStoreItem)
        .filter(ArchiveStoreItem.sha256_hash == sha256)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Datei existiert bereits.",
        )

    if len(description) < UPLOAD_DESC_MIN or len(description) > UPLOAD_DESC_MAX:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Beschreibung muss {UPLOAD_DESC_MIN}-"
                f"{UPLOAD_DESC_MAX} Zeichen lang sein."
            ),
        )

    key = f"{S3_PATH_ARCHIVE_STORE}/{sha256}"
    storage.upload(
        key,
        content,
        file.content_type or "application/octet-stream",
    )

    now = _now()
    store_item = ArchiveStoreItem(
        original_name=name,
        original_description=description,
        name=name,
        description=description,
        extension=ext,
        mime_type=file.content_type or "application/octet-stream",
        size=size,
        sha256_hash=sha256,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(store_item)
    db.flush()

    archive_file = ArchiveFile(
        archive_dir_id=0,
        description=description,
    )
    db.add(archive_file)
    db.flush()

    version = ArchiveFileVersion(
        archive_file_id=archive_file.id,
        archive_store_item_id=store_item.id,
        active=True,
    )
    db.add(version)
    db.commit()

    return _file_short(archive_file)


# --- Comments ---


def create_comment(
    db: Session,
    file_id: int,
    content: str,
    user_id: int,
) -> dict[str, object]:
    file_obj = db.get(ArchiveFile, file_id)
    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datei nicht gefunden.",
        )
    now = _now()
    comment = ArchiveFileComment(
        archive_file_id=file_id,
        content=content,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(comment)
    db.commit()
    return _comment_response(comment)


def delete_comment(
    db: Session,
    file_id: int,
    comment_id: int,
    user: Member,
) -> None:
    _require_admin(user)
    comment = db.get(ArchiveFileComment, comment_id)
    if not comment or comment.archive_file_id != file_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kommentar nicht gefunden.",
        )
    comment.deleted_at = _now()
    db.commit()


# --- Search ---

SEARCH_LIMIT = 50


def _dir_path_string(
    db: Session,
    dir_obj: ArchiveDir,
) -> str:
    parts = [str(p["name"]) for p in _build_path(db, dir_obj)]
    return " / ".join(parts) if parts else ""


def search_archive(
    db: Session,
    user: Member,
    query: str,
) -> list[dict[str, object]]:
    term = f"%{query}%"
    admin = is_archive_admin(user)
    results: list[dict[str, object]] = []

    dir_hits = (
        db.query(ArchiveDir)
        .filter(
            ArchiveDir.deleted_at.is_(None),
            ArchiveDir.archive_dir_id != 0,
            ArchiveDir.name.ilike(term),
        )
        .limit(SEARCH_LIMIT)
        .all()
    )
    for d in dir_hits:
        if not admin and not can_insight(user, db, d):
            continue
        results.append(
            {
                "type": "dir",
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "path": _dir_path_string(db, d),
            }
        )

    remaining = SEARCH_LIMIT - len(results)
    if remaining <= 0:
        return results

    file_hits = (
        db.query(ArchiveFile)
        .join(
            ArchiveFileVersion,
            (ArchiveFileVersion.archive_file_id == ArchiveFile.id)
            & (ArchiveFileVersion.active == True),  # noqa: E712
        )
        .join(
            ArchiveStoreItem,
            ArchiveStoreItem.id == ArchiveFileVersion.archive_store_item_id,
        )
        .filter(
            ArchiveFile.deleted_at.is_(None),
            (ArchiveStoreItem.name.ilike(term) | ArchiveFile.description.ilike(term)),
        )
        .limit(SEARCH_LIMIT)
        .all()
    )
    for f in file_hits:
        if len(results) >= SEARCH_LIMIT:
            break
        parent = db.get(
            ArchiveDir,
            f.archive_dir_id,
        )
        if (
            parent
            and not admin
            and not can_insight(
                user,
                db,
                parent,
            )
        ):
            continue
        item = _active_store_item(f)
        path = _dir_path_string(db, parent) if parent else "Archiv"
        results.append(
            {
                "type": "file",
                "id": f.id,
                "name": item.name if item else None,
                "description": f.description,
                "extension": (item.extension if item else None),
                "is_image": (item.is_image if item else False),
                "path": path,
            }
        )

    return results
