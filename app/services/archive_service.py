import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, literal, select

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
from app.models.archive_permission import (
    ArchivePermission,
)
from app.models.archive_store_item import (
    ArchiveStoreItem,
)
from app.models.org import Org
from app.models.state import State
from app.services.permission_service import (
    calculate_permissions,
)
from app.services.search_utils import build_prefix_tsquery_text

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from app.models.member import Member

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


# --- Permissions ---


def is_archive_admin(user: Member) -> bool:
    return "archiveAdmin" in calculate_permissions(user)


@dataclass(frozen=True)
class _PermSets:
    """Snapshot of valid org/state IDs, loaded once per request instead of
    once per directory row (previously N+1 queries in directory listings)."""

    orgs: frozenset[str]
    states: frozenset[str]


def _load_perm_sets(db: Session) -> _PermSets:
    return _PermSets(
        orgs=frozenset(o.id for o in db.query(Org).all()),
        states=frozenset(s.id for s in db.query(State).all()),
    )


def get_effective_permissions(
    db: Session,
    dir_obj: ArchiveDir,
    perm_sets: _PermSets,
) -> list[str]:
    own = _own_permissions(dir_obj, perm_sets)
    inherited = _inherited_permissions(db, dir_obj, perm_sets)
    merged = list(set(own) | set(inherited))
    merged.sort()
    return merged


def _own_permissions(
    dir_obj: ArchiveDir,
    perm_sets: _PermSets,
) -> list[str]:
    result = []
    # Skip permissions referencing deleted orgs/states
    for p in dir_obj.archive_permissions:
        if p.org_id in perm_sets.orgs and p.state_id in perm_sets.states:
            key = f"{p.org_id}_{p.state_id}"
            if key not in result:
                result.append(key)
    return result


def _inherited_permissions(
    db: Session,
    dir_obj: ArchiveDir,
    perm_sets: _PermSets,
) -> list[str]:
    parent = (
        db.get(ArchiveDir, dir_obj.archive_dir_id)
        if dir_obj.archive_dir_id is not None
        else None
    )
    if not parent:
        return []
    if parent.recursive_permissions:
        return get_effective_permissions(db, parent, perm_sets)
    return _inherited_permissions(db, parent, perm_sets)


def can_insight(
    user: Member,
    db: Session,
    dir_obj: ArchiveDir,
    perm_sets: _PermSets,
) -> bool:
    key = f"{user.org_id}_{user.state_id}"
    return key in get_effective_permissions(db, dir_obj, perm_sets)


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
    perm_sets: _PermSets,
) -> None:
    if is_archive_admin(user):
        return
    if not can_insight(user, db, dir_obj, perm_sets):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Keine Berechtigung für dieses Verzeichnis.",
        )


# --- File helpers ---


def _file_short(
    file_obj: ArchiveFile,
) -> dict[str, object]:
    item = file_obj.store_item
    return {
        "type": "file",
        "id": file_obj.id,
        "name": item.name,
        "extension": item.extension,
        "description": file_obj.description,
        "size": item.size,
        "is_image": item.is_image,
        "mime_type": item.mime_type,
        "created_at": item.created_at,
        "deleted_at": file_obj.deleted_at,
    }


def _dir_short(d: ArchiveDir) -> dict[str, object]:
    return {
        "type": "dir",
        "id": d.id,
        "name": d.name,
        "description": d.description,
        "created_at": d.created_at,
        "deleted_at": d.deleted_at,
    }


def build_path(
    db: Session,
    dir_obj: ArchiveDir,
) -> list[dict[str, object]]:
    path = []
    current: ArchiveDir | None = dir_obj
    while current is not None:
        path.append({"id": current.id, "name": current.name})
        current = (
            db.get(ArchiveDir, current.archive_dir_id)
            if current.archive_dir_id is not None
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
        "created_at": item.created_at,
    }


def _comment_response(
    c: ArchiveFileComment,
) -> dict[str, object]:
    return {
        "id": c.id,
        "content": c.content,
        "author": c.member.cn if c.member else None,
        "created_at": c.created_at,
    }


# --- Dir operations ---


def _empty_content() -> dict[str, dict[str, list[dict[str, object]]]]:
    return {
        "subdirs": {"insight": [], "admin": [], "trashed": []},
        "files": {"insight": [], "admin": [], "trashed": []},
    }


def _classify_dir(
    d: ArchiveDir,
    *,
    admin: bool,
    user: Member,
    db: Session,
    bucket: dict[str, list[dict[str, object]]],
    perm_sets: _PermSets,
) -> None:
    if d.deleted_at:
        if admin:
            bucket["trashed"].append(_dir_short(d))
        return
    if can_insight(user, db, d, perm_sets):
        bucket["insight"].append(_dir_short(d))
    elif admin:
        bucket["admin"].append(_dir_short(d))


def get_root_content(
    db: Session,
    user: Member,
) -> dict[str, object]:
    admin = is_archive_admin(user)
    perm_sets = _load_perm_sets(db)
    content = _empty_content()

    dirs = (
        db.query(ArchiveDir)
        .filter(ArchiveDir.archive_dir_id.is_(None))
        .order_by(ArchiveDir.name)
        .all()
    )
    for d in dirs:
        _classify_dir(
            d,
            admin=admin,
            user=user,
            db=db,
            bucket=content["subdirs"],
            perm_sets=perm_sets,
        )

    files = db.query(ArchiveFile).filter(ArchiveFile.archive_dir_id.is_(None)).all()
    for f in files:
        if f.deleted_at:
            if admin:
                content["files"]["trashed"].append(_file_short(f))
            continue
        if admin:
            content["files"]["admin"].append(_file_short(f))

    return {
        "type": "dir",
        "id": None,
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
        "stats": get_archive_stats(db),
        "created_at": None,
        "updated_at": None,
        "deleted_at": None,
    }


def _build_dir_detail_content(
    db: Session,
    dir_obj: ArchiveDir,
    user: Member,
    admin: bool,  # noqa: FBT001
    perm_sets: _PermSets,
) -> dict[str, dict[str, list[dict[str, object]]]]:
    content = _empty_content()

    children = dir_obj.children.order_by(ArchiveDir.name).all()
    for d in children:
        _classify_dir(
            d,
            admin=admin,
            user=user,
            db=db,
            bucket=content["subdirs"],
            perm_sets=perm_sets,
        )

    has_insight = can_insight(user, db, dir_obj, perm_sets)
    files = dir_obj.archive_files.all()
    for f in files:
        if f.deleted_at:
            if admin:
                content["files"]["trashed"].append(_file_short(f))
            continue
        if has_insight:
            content["files"]["insight"].append(_file_short(f))
        elif admin:
            content["files"]["admin"].append(_file_short(f))

    return content


def get_dir_detail(
    db: Session,
    dir_id: uuid.UUID,
    user: Member,
) -> dict[str, object]:
    dir_obj = db.get(ArchiveDir, dir_id)
    if not dir_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Verzeichnis nicht gefunden.",
        )
    perm_sets = _load_perm_sets(db)
    _require_insight_or_admin(user, db, dir_obj, perm_sets)

    admin = is_archive_admin(user)
    content = _build_dir_detail_content(db, dir_obj, user, admin, perm_sets)

    own = _own_permissions(dir_obj, perm_sets)
    inherited = _inherited_permissions(db, dir_obj, perm_sets)
    effective = list(set(own) | set(inherited))
    effective.sort()

    return {
        "type": "dir",
        "id": dir_obj.id,
        "name": dir_obj.name,
        "description": dir_obj.description,
        "path": build_path(db, dir_obj),
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
        "stats": None,
        "created_at": dir_obj.created_at,
        "updated_at": dir_obj.updated_at,
        "deleted_at": dir_obj.deleted_at,
    }


def create_dir(
    db: Session,
    data: dict[str, object],
    user: Member,
) -> ArchiveDir:
    _require_admin(user)
    parent_id = data.get("parentId")
    if parent_id is not None:
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
    dir_id: uuid.UUID,
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
    perms_raw = data.get("permissions", [])
    perms = list(perms_raw) if isinstance(perms_raw, list) else []
    _sync_permissions(db, dir_obj, [str(p) for p in perms])
    db.commit()
    return dir_obj


def _dir_has_content(db: Session, dir_id: uuid.UUID) -> bool:
    """True if dir_id has any child dir or file row, regardless of their own
    deleted_at status. Both archive_dir_id FKs are ON DELETE RESTRICT, so
    the database itself would already refuse a hard-delete here as a
    second line of defense — this check exists to turn that into a clean
    409/soft-delete instead of a raw IntegrityError.
    """
    has_children = (
        db.query(ArchiveDir).filter(ArchiveDir.archive_dir_id == dir_id).count() > 0
    )
    has_files = (
        db.query(ArchiveFile).filter(ArchiveFile.archive_dir_id == dir_id).count() > 0
    )
    return has_children or has_files


def delete_dir(
    db: Session,
    dir_id: uuid.UUID,
    user: Member,
) -> None:
    _require_admin(user)
    dir_obj = db.get(ArchiveDir, dir_id)
    if not dir_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Verzeichnis nicht gefunden.",
        )

    if _dir_has_content(db, dir_obj.id):
        # Intentional: only the DB row is soft-deleted. The S3 object is never
        # removed — files in the content-addressed store are kept indefinitely.
        dir_obj.deleted_at = _now()
    else:
        db.delete(dir_obj)
    db.commit()


def purge_dir(
    db: Session,
    dir_id: uuid.UUID,
    user: Member,
) -> None:
    """Permanently deletes an empty, soft-deleted directory. Unlike
    delete_dir() (which silently hard-deletes any already-empty directory,
    trashed or not), this requires the directory to currently be in the
    trash and errors explicitly (409) instead of silently no-op'ing again
    as a soft-delete when the emptiness check fails.
    """
    _require_admin(user)
    dir_obj = db.get(ArchiveDir, dir_id)
    if not dir_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Verzeichnis nicht gefunden.",
        )
    if dir_obj.deleted_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Verzeichnis befindet sich nicht im Papierkorb.",
        )
    if _dir_has_content(db, dir_obj.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Verzeichnis ist nicht leer und kann nicht endgültig gelöscht werden."
            ),
        )
    db.delete(dir_obj)
    db.commit()


def restore_dir(
    db: Session,
    dir_id: uuid.UUID,
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
    db.commit()


def _receive_dirs(
    db: Session,
    target_dir_id: uuid.UUID | None,
    dir_ids: list[uuid.UUID],
) -> None:
    for did in dir_ids:
        d = db.get(ArchiveDir, did)
        if not d:
            continue
        if did == target_dir_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Verzeichnis kann nicht in sich selbst verschoben werden.",
            )
        if target_dir_id is not None and _is_descendant(db, target_dir_id, did):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "Verzeichnis kann nicht in eigenes Unterverzeichnis "
                    "verschoben werden."
                ),
            )
        d.archive_dir_id = target_dir_id


def _receive_files(
    db: Session,
    target_dir_id: uuid.UUID | None,
    file_ids: list[uuid.UUID],
) -> None:
    for fid in file_ids:
        f = db.get(ArchiveFile, fid)
        if not f:
            continue
        f.archive_dir_id = target_dir_id


def receive_items(
    db: Session,
    target_dir_id: uuid.UUID | None,
    item_type: str,
    item_ids: list[uuid.UUID],
    user: Member,
) -> None:
    _require_admin(user)

    if target_dir_id is not None:
        target = db.get(ArchiveDir, target_dir_id)
        if not target:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Zielverzeichnis nicht gefunden.",
            )

    if item_type == "dir":
        _receive_dirs(db, target_dir_id, item_ids)
    else:
        _receive_files(db, target_dir_id, item_ids)

    db.commit()


def _is_descendant(
    db: Session,
    candidate_id: uuid.UUID,
    ancestor_id: uuid.UUID,
) -> bool:
    current_id: uuid.UUID | None = candidate_id
    visited: set[uuid.UUID] = set()
    while current_id is not None and current_id not in visited:
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
    file_id: uuid.UUID,
    user: Member,
) -> dict[str, object]:
    file_obj = db.get(ArchiveFile, file_id)
    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datei nicht gefunden.",
        )

    if file_obj.archive_dir_id is not None:
        dir_obj = db.get(ArchiveDir, file_obj.archive_dir_id)
        if dir_obj:
            _require_insight_or_admin(user, db, dir_obj, _load_perm_sets(db))
    elif not is_archive_admin(user):
        # archive_dir_id IS NULL means "unsorted upload" - no real
        # ArchiveDir row to check can_insight() against. Admin-only
        # everywhere else in this module (get_root_content(),
        # search_archive(), create_comment()), so viewing one follows the
        # same rule.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Keine Berechtigung für diese Datei.",
        )

    admin = is_archive_admin(user)
    item = file_obj.store_item
    path = []
    if file_obj.archive_dir_id is not None:
        dir_obj = db.get(ArchiveDir, file_obj.archive_dir_id)
        if dir_obj:
            path = build_path(db, dir_obj)

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
        "archive_dir_id": file_obj.archive_dir_id,
        "name": item.name,
        "extension": item.extension,
        "description": file_obj.description,
        "size": item.size,
        "is_image": item.is_image,
        "mime_type": item.mime_type,
        "path": path,
        "active_version": _store_item_response(item),
        "comments": [_comment_response(c) for c in comments],
        "trashed_comments": [_comment_response(c) for c in trashed_comments],
        "created_at": item.created_at,
        "deleted_at": file_obj.deleted_at,
    }


def update_file(
    db: Session,
    file_id: uuid.UUID,
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
    file_id: uuid.UUID,
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
    file_id: uuid.UUID,
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


def get_presigned_url(
    db: Session,
    file_id: uuid.UUID,
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

    if file_obj.archive_dir_id is not None:
        dir_obj = db.get(ArchiveDir, file_obj.archive_dir_id)
        if dir_obj:
            _require_insight_or_admin(user, db, dir_obj, _load_perm_sets(db))

    item = file_obj.store_item
    sha256_hash, is_image = item.sha256_hash, item.is_image
    name, extension, mime_type = item.name, item.extension, item.mime_type
    # Everything from here on only talks to S3 (cache check, presigned-URL
    # generation, possibly resize+reupload on a cache miss) — release the
    # pooled DB connection first instead of holding it for that whole,
    # potentially slow round-trip. Under a burst of concurrent requests
    # (e.g. opening a directory listing with hundreds of trashed files,
    # each triggering a thumbnail load), holding the connection open for
    # the S3 duration instead of just the few ms of DB access is what
    # exhausts the pool — see incident 2026-08-01.
    db.close()

    if size and size in THUMB_SIZES and is_image:
        _get_or_create_thumbnail(sha256_hash, size, storage)
        return storage.generate_presigned_url(
            _thumbnail_cache_key(sha256_hash, size),
            content_type="image/jpeg",
        )

    key = f"{S3_PATH_ARCHIVE_STORE}/{sha256_hash}"
    filename = f"{name}.{extension}"
    return storage.generate_presigned_url(
        key,
        filename=filename,
        content_type=mime_type,
    )


def _thumbnail_cache_key(sha256_hash: str, size: str) -> str:
    return (
        f"{S3_PATH_ARCHIVE_CACHE}/{sha256_hash}.{THUMBNAIL_CACHE_VERSION}.thumb_{size}"
    )


def _get_or_create_thumbnail(
    sha256_hash: str,
    size: str,
    storage: StorageClient,
) -> bytes | None:
    cache_key = _thumbnail_cache_key(sha256_hash, size)
    if storage.exists(cache_key):
        return storage.download(cache_key)

    source_key = f"{S3_PATH_ARCHIVE_STORE}/{sha256_hash}"
    if not storage.exists(source_key):
        return None

    try:
        data = storage.download(source_key)
        thumb_bytes, content_type = generate_thumbnail(
            data,
            THUMB_SIZES[size],
        )
        storage.upload(cache_key, thumb_bytes, content_type)
    except OSError, ValueError:
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
    user_id: uuid.UUID,
) -> list[dict[str, object]]:
    files = (
        db.query(ArchiveFile)
        .join(
            ArchiveStoreItem,
            ArchiveFile.archive_store_item_id == ArchiveStoreItem.id,
        )
        .filter(
            ArchiveFile.archive_dir_id.is_(None),
            ArchiveStoreItem.created_by == user_id,
        )
        .all()
    )
    return [_file_short(f) for f in files]


def get_unsorted_upload_count(db: Session) -> int:
    """Org-wide count of files not yet filed into any directory.

    Unlike get_unfiled_uploads(), this is not scoped to a single uploader -
    used for the weekly archive health-check report to all archive admins.
    """
    return db.query(ArchiveFile).filter(ArchiveFile.archive_dir_id.is_(None)).count()


def get_archive_stats(db: Session) -> dict[str, object]:
    """Archive-wide counts shown in the root directory's info card, which
    otherwise has nothing to show there (permissions are meaningless at
    the synthetic root). "Effectively present" here means reachable
    through normal browsing - not deleted, not unfiled, and not sitting
    under a trashed ancestor - independent of the caller's own org/state
    permissions (this is a coarse, permission-agnostic overview, not a
    per-user visibility count).

    delete_dir() never cascades deleted_at onto children when trashing a
    non-empty directory (see _dir_has_content()'s docstring) - a
    subdirectory or file can therefore have deleted_at IS NULL while an
    ancestor is trashed, making it reachable only via the trash UI, not
    normal navigation. trashed_subtree resolves this once via a recursive
    CTE (trashed dirs plus every descendant of a trashed dir) and is
    reused below wherever a directory/file's reachability matters.

    unique_object_count/total_size/by_extension are additionally scoped to
    store items referenced by at least one such reachable file - the same
    scope as file_count - since many files can share one object but never
    the reverse, so unique_object_count must never exceed file_count.
    """
    trashed_base = select(ArchiveDir.id).where(ArchiveDir.deleted_at.isnot(None))
    trashed_subtree = trashed_base.cte(
        name="archive_stats_trashed_subtree", recursive=True
    )
    trashed_subtree = trashed_subtree.union(
        select(ArchiveDir.id).join(
            trashed_subtree, ArchiveDir.archive_dir_id == trashed_subtree.c.id
        )
    )
    under_trash = select(trashed_subtree.c.id)

    active_file_exists = (
        db.query(ArchiveFile.id)
        .filter(
            ArchiveFile.archive_store_item_id == ArchiveStoreItem.id,
            ArchiveFile.deleted_at.is_(None),
            ArchiveFile.archive_dir_id.is_not(None),
            ~ArchiveFile.archive_dir_id.in_(under_trash),
        )
        .correlate(ArchiveStoreItem)
        .exists()
    )

    file_count = (
        db.query(ArchiveFile)
        .filter(
            ArchiveFile.deleted_at.is_(None),
            ArchiveFile.archive_dir_id.is_not(None),
            ~ArchiveFile.archive_dir_id.in_(under_trash),
        )
        .count()
    )
    dir_count = (
        db.query(ArchiveDir)
        .filter(ArchiveDir.deleted_at.is_(None), ~ArchiveDir.id.in_(under_trash))
        .count()
    )

    unique_object_count, total_size = (
        db.query(
            func.count(ArchiveStoreItem.id),
            func.coalesce(func.sum(ArchiveStoreItem.size), 0),
        )
        .filter(active_file_exists)
        .one()
    )

    by_extension = (
        db.query(
            ArchiveStoreItem.extension,
            func.count(ArchiveStoreItem.id),
            func.coalesce(func.sum(ArchiveStoreItem.size), 0),
        )
        .filter(active_file_exists)
        .group_by(ArchiveStoreItem.extension)
        .order_by(func.count(ArchiveStoreItem.id).desc())
        .all()
    )

    return {
        "file_count": file_count,
        "unique_object_count": unique_object_count,
        "dir_count": dir_count,
        "total_size": int(total_size),
        "by_extension": [
            {"extension": extension, "count": count, "size": int(size)}
            for extension, count, size in by_extension
        ],
    }


def upload_file(
    db: Session,
    file: UploadFile,
    description: str,
    user_id: uuid.UUID,
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
        archive_dir_id=None,
        description=description,
        archive_store_item_id=store_item.id,
        created_at=now,
        updated_at=now,
    )
    db.add(archive_file)
    db.commit()

    return _file_short(archive_file)


# --- Comments ---


def create_comment(
    db: Session,
    file_id: uuid.UUID,
    content: str,
    user: Member,
) -> dict[str, object]:
    file_obj = db.get(ArchiveFile, file_id)
    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datei nicht gefunden.",
        )

    # archive_dir_id IS NULL means "unsorted upload" - no real ArchiveDir
    # row to check can_insight() against. Those files are archiveAdmin-only
    # everywhere else in this module (get_root_content(), search_archive()),
    # so commenting on one follows the same rule.
    parent = (
        db.get(ArchiveDir, file_obj.archive_dir_id)
        if file_obj.archive_dir_id is not None
        else None
    )
    if parent is not None:
        _require_insight_or_admin(user, db, parent, _load_perm_sets(db))
    elif not is_archive_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Keine Berechtigung für diese Datei.",
        )

    now = _now()
    comment = ArchiveFileComment(
        archive_file_id=file_obj.id,
        content=content,
        created_by=user.id_uuid,
        created_at=now,
        updated_at=now,
    )
    db.add(comment)
    db.commit()
    return _comment_response(comment)


def delete_comment(
    db: Session,
    file_id: uuid.UUID,
    comment_id: uuid.UUID,
    user: Member,
) -> None:
    file_obj = db.get(ArchiveFile, file_id)
    comment = db.get(ArchiveFileComment, comment_id)
    if not file_obj or not comment or comment.archive_file_id != file_obj.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kommentar nicht gefunden.",
        )
    if comment.created_by != user.id_uuid and not is_archive_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Berechtigung: eigener Kommentar oder archiveAdmin",
        )
    comment.deleted_at = _now()
    db.commit()


# --- Search ---

SEARCH_LIMIT = 50


def dir_path_string(
    db: Session,
    dir_obj: ArchiveDir,
) -> str:
    parts = [str(p["name"]) for p in build_path(db, dir_obj)]
    return " / ".join(parts) if parts else ""


def _collect_dir_hits(
    db: Session,
    user: Member,
    hits: list[tuple[ArchiveDir, float]],
    admin: bool,  # noqa: FBT001
    perm_sets: _PermSets,
) -> list[tuple[float, dict[str, object]]]:
    """Applies the same insight-permission check to a ranked list of dir
    hits and shapes each into the search result dict - shared between the
    exact (Stage 1) and fuzzy (Stage 2) search paths below."""
    collected: list[tuple[float, dict[str, object]]] = []
    for d, rank in hits:
        if not admin and not can_insight(user, db, d, perm_sets):
            continue
        collected.append(
            (
                rank,
                {
                    "type": "dir",
                    "id": d.id,
                    "name": d.name,
                    "description": d.description,
                    "path": dir_path_string(db, d),
                },
            )
        )
    return collected


def _collect_file_hits(
    db: Session,
    user: Member,
    hits: list[tuple[ArchiveFile, float]],
    admin: bool,  # noqa: FBT001
    perm_sets: _PermSets,
) -> list[tuple[float, dict[str, object]]]:
    """Same as _collect_dir_hits(), for files - including the unsorted-
    upload (archive_dir_id IS NULL, no resolvable parent) admin-only
    rule."""
    collected: list[tuple[float, dict[str, object]]] = []
    for f, rank in hits:
        parent = (
            db.get(ArchiveDir, f.archive_dir_id)
            if f.archive_dir_id is not None
            else None
        )
        # No resolvable parent means an unsorted upload sitting directly at
        # the root (archive_dir_id IS NULL - see get_root_content()) —
        # those are archiveAdmin-only, same as in regular directory
        # browsing, so a missing parent must NOT skip the permission check
        # (that would silently show unsorted uploads to every authenticated
        # user, admin or not).
        if not admin and (
            parent is None or not can_insight(user, db, parent, perm_sets)
        ):
            continue
        item = f.store_item
        path = dir_path_string(db, parent) if parent else "Archiv"
        collected.append(
            (
                rank,
                {
                    "type": "file",
                    "id": f.id,
                    "name": item.name,
                    "description": f.description,
                    "extension": item.extension,
                    "is_image": item.is_image,
                    "path": path,
                },
            )
        )
    return collected


def _search_dirs_exact(db: Session, tsquery: object) -> list[tuple[ArchiveDir, float]]:
    rank = func.ts_rank(ArchiveDir.search_vector, tsquery)
    rows = (
        db.query(ArchiveDir, rank)
        .filter(
            ArchiveDir.deleted_at.is_(None),
            ArchiveDir.search_vector.op("@@")(tsquery),
        )
        .order_by(rank.desc())
        .limit(SEARCH_LIMIT)
        .all()
    )
    # SQLAlchemy's Row type doesn't carry the SQL-side float type of a
    # func.ts_rank() expression into Python - the explicit float() cast
    # here is what keeps the return type free of Any, not a runtime need.
    return [(d, float(r)) for d, r in rows]


def _search_files_exact(
    db: Session, tsquery: object
) -> list[tuple[ArchiveFile, float]]:
    # EXISTS (not a join) - a file with several matching comments must
    # still only appear once. The paired MAX(ts_rank(...)) subquery feeds
    # the same "at least one comment matched" signal into ranking,
    # weighted lowest (D) of the three possible match sources.
    comment_match_exists = (
        db.query(ArchiveFileComment.id)
        .filter(
            ArchiveFileComment.archive_file_id == ArchiveFile.id,
            ArchiveFileComment.deleted_at.is_(None),
            ArchiveFileComment.search_vector.op("@@")(tsquery),
        )
        .correlate(ArchiveFile)
        .exists()
    )
    comment_rank = (
        db.query(func.max(func.ts_rank(ArchiveFileComment.search_vector, tsquery)))
        .filter(
            ArchiveFileComment.archive_file_id == ArchiveFile.id,
            ArchiveFileComment.deleted_at.is_(None),
            ArchiveFileComment.search_vector.op("@@")(tsquery),
        )
        .correlate(ArchiveFile)
        .scalar_subquery()
    )
    rank = func.greatest(
        func.ts_rank(ArchiveStoreItem.search_vector, tsquery),
        func.ts_rank(ArchiveFile.search_vector, tsquery),
        func.coalesce(comment_rank, 0.0),
    )
    rows = (
        db.query(ArchiveFile, rank)
        .join(
            ArchiveStoreItem,
            ArchiveStoreItem.id == ArchiveFile.archive_store_item_id,
        )
        .filter(
            ArchiveFile.deleted_at.is_(None),
            (
                ArchiveStoreItem.search_vector.op("@@")(tsquery)
                | ArchiveFile.search_vector.op("@@")(tsquery)
                | comment_match_exists
            ),
        )
        .order_by(rank.desc())
        .limit(SEARCH_LIMIT)
        .all()
    )
    return [(f, float(r)) for f, r in rows]


def _search_dirs_fuzzy(db: Session, query: str) -> list[tuple[ArchiveDir, float]]:
    """Typo-tolerant fallback (pg_trgm Stage 2) - only ever called when
    _search_dirs_exact()/_search_files_exact() found nothing at all, see
    search_archive(). word_similarity() (not similarity()) is deliberate:
    it finds the best-matching *substring*, so a short, typo'd query still
    scores well against a long description - plain similarity() compares
    whole strings and would score a real typo match far too low to pass
    the threshold. The `<%` operator applies Postgres's own configured
    pg_trgm.word_similarity_threshold (0.6 by default) and is the
    GIN-trgm-indexable form of that same check.
    """
    q = literal(query)
    rank = func.greatest(
        func.word_similarity(query, ArchiveDir.name),
        func.word_similarity(query, func.coalesce(ArchiveDir.description, "")),
    )
    rows = (
        db.query(ArchiveDir, rank)
        .filter(
            ArchiveDir.deleted_at.is_(None),
            (
                q.op("<%")(ArchiveDir.name)
                | q.op("<%")(func.coalesce(ArchiveDir.description, ""))
            ),
        )
        .order_by(rank.desc())
        .limit(SEARCH_LIMIT)
        .all()
    )
    return [(d, float(r)) for d, r in rows]


def _search_files_fuzzy(db: Session, query: str) -> list[tuple[ArchiveFile, float]]:
    """Typo-tolerant fallback (pg_trgm Stage 2) for files - see
    _search_dirs_fuzzy() for the word_similarity()-vs-similarity()
    reasoning. Same EXISTS + MAX(word_similarity(...)) shape as
    _search_files_exact()'s comment handling, just swapping the matching
    primitive."""
    q = literal(query)
    comment_match = (
        db.query(ArchiveFileComment.id)
        .filter(
            ArchiveFileComment.archive_file_id == ArchiveFile.id,
            ArchiveFileComment.deleted_at.is_(None),
            q.op("<%")(ArchiveFileComment.content),
        )
        .correlate(ArchiveFile)
        .exists()
    )
    comment_rank = (
        db.query(func.max(func.word_similarity(query, ArchiveFileComment.content)))
        .filter(
            ArchiveFileComment.archive_file_id == ArchiveFile.id,
            ArchiveFileComment.deleted_at.is_(None),
            q.op("<%")(ArchiveFileComment.content),
        )
        .correlate(ArchiveFile)
        .scalar_subquery()
    )
    rank = func.greatest(
        func.word_similarity(query, ArchiveStoreItem.name),
        func.word_similarity(query, ArchiveStoreItem.extension),
        func.word_similarity(query, func.coalesce(ArchiveFile.description, "")),
        func.coalesce(comment_rank, 0.0),
    )
    rows = (
        db.query(ArchiveFile, rank)
        .join(
            ArchiveStoreItem,
            ArchiveStoreItem.id == ArchiveFile.archive_store_item_id,
        )
        .filter(
            ArchiveFile.deleted_at.is_(None),
            (
                q.op("<%")(ArchiveStoreItem.name)
                | q.op("<%")(ArchiveStoreItem.extension)
                | q.op("<%")(func.coalesce(ArchiveFile.description, ""))
                | comment_match
            ),
        )
        .order_by(rank.desc())
        .limit(SEARCH_LIMIT)
        .all()
    )
    return [(f, float(r)) for f, r in rows]


_FUZZY_MIN_QUERY_LENGTH = 3


def search_archive(
    db: Session,
    user: Member,
    query: str,
) -> list[dict[str, object]]:
    """Ranked full-text search across directories and files.

    Stage 1 (exact/prefix): see build_prefix_tsquery_text() (search_utils.py) for the
    query-construction reasoning. Ranking weights (highest to lowest,
    shared across all four searched tables): name (A) > description (B) >
    extension (C) > comment content (D) - a name match ranks above an
    incidental comment mention.

    Stage 2 (pg_trgm typo tolerance): only runs when Stage 1 finds
    nothing at all - an exact/prefix hit never gets displaced by a fuzzier
    match, see _search_dirs_fuzzy()/_search_files_fuzzy(). Gated to
    queries of at least _FUZZY_MIN_QUERY_LENGTH characters: trigram
    similarity on a 1-2 character query is not meaningfully "fuzzy" - it
    coincidentally scores a perfect 1.0 against any text merely containing
    that short fragment as an isolated word (verified empirically:
    word_similarity('im', 'Irgendein Verzeichnis im Archiv') = 1.0, purely
    because "im" is itself a common German preposition, not a typo of
    anything). Without this gate, the router's min_length=2 would let
    every short stopword flood Stage 2 with coincidental matches.

    Dirs and files are merged into one relevance-sorted list; SEARCH_LIMIT
    is applied per source table before permission filtering and again to
    the final merged list (a heavily filtered top-SEARCH_LIMIT window can
    still return fewer than SEARCH_LIMIT results - an accepted,
    pre-existing trade-off).
    """
    admin = is_archive_admin(user)
    perm_sets = _load_perm_sets(db)

    tsquery_text = build_prefix_tsquery_text(db, query)
    ranked_results: list[tuple[float, dict[str, object]]] = []
    if tsquery_text:
        tsquery = func.to_tsquery("german", tsquery_text)
        ranked_results = _collect_dir_hits(
            db, user, _search_dirs_exact(db, tsquery), admin, perm_sets
        )
        ranked_results += _collect_file_hits(
            db, user, _search_files_exact(db, tsquery), admin, perm_sets
        )

    if not ranked_results and len(query.strip()) >= _FUZZY_MIN_QUERY_LENGTH:
        ranked_results = _collect_dir_hits(
            db, user, _search_dirs_fuzzy(db, query), admin, perm_sets
        )
        ranked_results += _collect_file_hits(
            db, user, _search_files_fuzzy(db, query), admin, perm_sets
        )

    ranked_results.sort(key=lambda entry: entry[0], reverse=True)
    return [result for _, result in ranked_results[:SEARCH_LIMIT]]
