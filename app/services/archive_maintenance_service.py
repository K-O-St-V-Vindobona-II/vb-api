"""Maintenance operations (hard-delete, restore, duplicate inspection) for
soft-deleted archive files.

CLI-only, deliberately. This module must never be imported from anything
under app/api/ — the only entry point is
scripts/maintain_deleted_archive_files.py, run manually by an operator.
There is no HTTP endpoint for any of this and there must never be one.
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, cast

from botocore.exceptions import ClientError
from sqlalchemy import func

from app.core.storage import S3_PATH_ARCHIVE_CACHE, S3_PATH_ARCHIVE_STORE, StorageClient
from app.models.archive_dir import ArchiveDir
from app.models.archive_file import ArchiveFile
from app.models.archive_store_item import ArchiveStoreItem
from app.services.archive_service import dir_path_string

if TYPE_CHECKING:
    import uuid
    from datetime import datetime

    from sqlalchemy.orm import Session


class PurgeImpact(Enum):
    """Whether purging a candidate would also remove its store item's S3
    object, derived from how many OTHER ArchiveFile rows (active vs.
    soft-deleted) still reference the same archive_store_item_id.
    """

    DUPLICATE = auto()
    """At least one active file still references the same content — S3 is
    never touched by purging this candidate."""

    SHARED = auto()
    """No active file, but at least one other soft-deleted file still
    references the same content — S3 stays untouched for now, but will be
    removed once the last referencing file is purged."""

    SOLE = auto()
    """No other file, active or deleted, references the same content —
    purging this candidate deletes the underlying S3 object immediately."""


def _classify_impact(active_count: int, other_deleted_count: int) -> PurgeImpact:
    if active_count > 0:
        return PurgeImpact.DUPLICATE
    if other_deleted_count > 0:
        return PurgeImpact.SHARED
    return PurgeImpact.SOLE


@dataclass(frozen=True)
class PurgeCandidate:
    file_id: int
    path: str
    name: str
    extension: str
    description: str | None
    deleted_at: datetime
    size: int
    sha256_hash: str
    archive_store_item_id: uuid.UUID
    archive_dir_id: int | None = None
    active_sibling_count: int = 0
    other_deleted_sibling_count: int = 0

    @property
    def filename(self) -> str:
        return f"{self.name}.{self.extension}"

    @property
    def impact(self) -> PurgeImpact:
        return _classify_impact(
            self.active_sibling_count, self.other_deleted_sibling_count
        )


@dataclass(frozen=True)
class PurgeResult:
    file_id: int
    store_item_deleted: bool
    s3_keys_deleted: list[str]
    s3_errors: list[str]


@dataclass(frozen=True)
class FileLocation:
    """Bare file id + display location, used wherever a full PurgeCandidate
    (which implies a currently soft-deleted purge candidate) would be the
    wrong type — restore targets and `--file`-inspected files in
    list-duplicates can be active files too, hence the explicit `deleted`
    flag rather than assuming it from context.
    """

    file_id: int
    path: str
    name: str
    extension: str
    deleted: bool

    @property
    def filename(self) -> str:
        return f"{self.name}.{self.extension}"


@dataclass(frozen=True)
class ActiveDuplicate:
    file_id: int
    path: str
    name: str
    extension: str

    @property
    def filename(self) -> str:
        return f"{self.name}.{self.extension}"


@dataclass(frozen=True)
class DirLocation:
    dir_id: int
    path: str
    deleted: bool


class ArchiveMaintenanceError(RuntimeError):
    """Raised when a CLI archive-maintenance operation cannot proceed: the
    given file id doesn't exist, or isn't in the required state (e.g. not
    currently soft-deleted for purge/restore targets)."""


def _describe_location(db: Session, file_obj: ArchiveFile) -> tuple[str, str, str]:
    """Returns (path, name, extension) for display purposes."""
    item = file_obj.store_item
    path = (
        dir_path_string(db, file_obj.archive_dir)
        if file_obj.archive_dir is not None
        else "Archiv"
    )
    return path, item.name, item.extension


def _reference_counts(
    db: Session, store_item_ids: set[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int]]:
    """Maps store_item_id -> (active_file_count, deleted_file_count) across
    ALL ArchiveFile rows referencing it, including the candidate(s) currently
    being listed. A single aggregated query regardless of how many store
    items are involved, so listing hundreds of candidates doesn't turn into
    hundreds of per-candidate queries.
    """
    if not store_item_ids:
        return {}
    rows = (
        db.query(
            ArchiveFile.archive_store_item_id,
            func.count().filter(ArchiveFile.deleted_at.is_(None)),
            func.count().filter(ArchiveFile.deleted_at.isnot(None)),
        )
        .filter(ArchiveFile.archive_store_item_id.in_(store_item_ids))
        .group_by(ArchiveFile.archive_store_item_id)
        .all()
    )
    return {store_item_id: (active, deleted) for store_item_id, active, deleted in rows}


def _to_candidate(
    db: Session, file_obj: ArchiveFile, active_count: int, deleted_count: int
) -> PurgeCandidate:
    path, name, extension = _describe_location(db, file_obj)
    item = file_obj.store_item
    return PurgeCandidate(
        file_id=file_obj.id,
        path=path,
        name=name,
        extension=extension,
        description=file_obj.description,
        deleted_at=cast("datetime", file_obj.deleted_at),
        size=item.size,
        sha256_hash=item.sha256_hash,
        archive_dir_id=file_obj.archive_dir_id,
        archive_store_item_id=file_obj.archive_store_item_id,
        active_sibling_count=active_count,
        other_deleted_sibling_count=deleted_count - 1,
    )


def _candidates_for(db: Session, files: list[ArchiveFile]) -> list[PurgeCandidate]:
    counts = _reference_counts(db, {f.archive_store_item_id for f in files})
    return [_to_candidate(db, f, *counts[f.archive_store_item_id]) for f in files]


def list_deleted_files(db: Session) -> list[PurgeCandidate]:
    """All currently soft-deleted archive files, oldest deletion first."""
    files = (
        db.query(ArchiveFile)
        .filter(ArchiveFile.deleted_at.isnot(None))
        .order_by(ArchiveFile.deleted_at)
        .all()
    )
    return _candidates_for(db, files)


def list_deleted_files_in_dir(db: Session, dir_id: int) -> list[PurgeCandidate]:
    """All currently soft-deleted archive files directly in the given
    directory (non-recursive — subdirectories are not included), oldest
    deletion first.
    """
    files = (
        db.query(ArchiveFile)
        .filter(
            ArchiveFile.archive_dir_id == dir_id,
            ArchiveFile.deleted_at.isnot(None),
        )
        .order_by(ArchiveFile.deleted_at)
        .all()
    )
    return _candidates_for(db, files)


def _live_sibling_counts(db: Session, store_item_id: uuid.UUID) -> tuple[int, int]:
    """Freshly (re-)computes (active_count, deleted_count) for a single
    store item, including the candidate's own row. Used by
    `is_still_duplicate()` immediately before an actual batch purge.
    """
    return _reference_counts(db, {store_item_id})[store_item_id]


def is_still_duplicate(db: Session, candidate: PurgeCandidate) -> bool:
    """Freshly re-checks, right before an actual batch purge, whether the
    candidate still has an active sibling referencing the same content. Used
    only on the batch (duplicate-only) purge path — a concurrent soft-delete
    of that active sibling between listing and purging must not silently
    turn a "no S3 risk" batch item into one that deletes the S3 object.
    """
    active_count, _ = _live_sibling_counts(db, candidate.archive_store_item_id)
    return active_count > 0


def _get_file(db: Session, file_id: int) -> ArchiveFile:
    """Shared existence check — raises regardless of soft-delete state."""
    file_obj = db.get(ArchiveFile, file_id)
    if file_obj is None:
        msg = f"Archivdatei {file_id} nicht gefunden."
        raise ArchiveMaintenanceError(msg)
    return file_obj


def _get_soft_deleted_file(db: Session, file_id: int) -> ArchiveFile:
    """Shared existence/state check for both purge and restore targets."""
    file_obj = _get_file(db, file_id)
    if file_obj.deleted_at is None:
        msg = f"Archivdatei {file_id} ist nicht gelöscht-markiert."
        raise ArchiveMaintenanceError(msg)
    return file_obj


def _delete_orphaned_store_items(
    db: Session, store_item_ids: set[uuid.UUID]
) -> list[ArchiveStoreItem]:
    """Deletes ArchiveStoreItem rows no longer referenced by any ArchiveFile.
    Must only be called after the owning ArchiveFile row(s) have already been
    removed AND committed.
    """
    orphaned: list[ArchiveStoreItem] = []
    for store_item_id in store_item_ids:
        still_referenced = (
            db.query(ArchiveFile)
            .filter(ArchiveFile.archive_store_item_id == store_item_id)
            .first()
            is not None
        )
        if still_referenced:
            continue
        # db.get() looks up by primary key, which is still the integer
        # `id` here - archive_store_items keeps its own Final-Cutover for a
        # later slice, so id_uuid must be queried explicitly instead.
        item = (
            db.query(ArchiveStoreItem)
            .filter(ArchiveStoreItem.id_uuid == store_item_id)
            .first()
        )
        if item is not None:
            orphaned.append(item)
            db.delete(item)
    return orphaned


def _delete_s3_key(
    storage: StorageClient,
    key: str,
    deleted: list[str],
    errors: list[str],
) -> None:
    try:
        storage.delete(key)
    except ClientError as exc:
        errors.append(f"{key}: {exc}")
        return
    deleted.append(key)


def _purge_s3_object(
    storage: StorageClient,
    sha256_hash: str,
    deleted: list[str],
    errors: list[str],
) -> None:
    """Removes the main store object and every cached thumbnail variant for
    a hash that is no longer referenced by any ArchiveFile. Uses a
    prefix listing for the cache instead of hardcoded sizes/versions, so a
    future THUMBNAIL_CACHE_VERSION bump or new thumbnail size can never
    leave orphaned cache entries behind.
    """
    _delete_s3_key(storage, f"{S3_PATH_ARCHIVE_STORE}/{sha256_hash}", deleted, errors)

    cache_prefix = f"{S3_PATH_ARCHIVE_CACHE}/{sha256_hash}."
    try:
        cache_keys = storage.list_keys(cache_prefix)
    except ClientError as exc:
        errors.append(f"list_keys({cache_prefix}): {exc}")
        return
    for key in cache_keys:
        _delete_s3_key(storage, key, deleted, errors)


def purge_file(db: Session, storage: StorageClient, file_id: int) -> PurgeResult:
    """Permanently deletes a soft-deleted archive file: hard-deletes the DB
    row (cascading its comments), then removes the underlying S3 object(s)
    if no other file still references the same content hash.

    DB changes are committed BEFORE any S3 deletion is attempted: if the S3
    step later fails, the worst case is an orphaned S3 object — a benign,
    self-diagnosable state already detected by check_s3_integrity.py. The
    reverse order risks a DB row surviving that wrongly believed an S3
    object it still needed was removed — silent data loss for other files
    that may still reference the same content hash.
    """
    file_obj = _get_soft_deleted_file(db, file_id)

    # Snapshot the store item before deleting. archive_store_item_id has no
    # unique constraint on ArchiveFile and can be shared across files (real
    # content dedup from the legacy migration), so the reference count must
    # not rely on "1 hash = 1 file".
    store_item_ids = {file_obj.archive_store_item_id}

    db.delete(file_obj)
    db.commit()

    orphaned_items = _delete_orphaned_store_items(db, store_item_ids)
    db.commit()

    s3_keys_deleted: list[str] = []
    s3_errors: list[str] = []
    for item in orphaned_items:
        _purge_s3_object(storage, item.sha256_hash, s3_keys_deleted, s3_errors)

    return PurgeResult(
        file_id=file_id,
        store_item_deleted=bool(orphaned_items),
        s3_keys_deleted=s3_keys_deleted,
        s3_errors=s3_errors,
    )


def restore_file(db: Session, file_id: int) -> FileLocation:
    """CLI-only restore: undoes a soft-delete by clearing deleted_at.

    Mirrors archive_service.restore_file()'s core logic (404 check,
    deleted_at = None, commit) minus its Member-based permission gate — this
    script already implies full DB access, same reasoning as purge_file()
    (see module docstring). Unlike the GUI version, this also requires the
    file to currently be soft-deleted (raises otherwise) instead of silently
    no-op'ing on an already-active file, consistent with this CLI's
    explicit-error philosophy elsewhere. `updated_at` is maintained
    automatically by the archive_files_set_updated_at DB trigger, not set
    here.

    Restricted to SOLE-impact files only (see PurgeImpact): if an active or
    another soft-deleted file still references the same content, restoring
    this one would resurrect a file whose content already survives (or will
    keep surviving) elsewhere, so it is rejected instead of silently
    restoring. Use the GUI for those cases.
    """
    file_obj = _get_soft_deleted_file(db, file_id)
    active_count, deleted_count = _live_sibling_counts(
        db, file_obj.archive_store_item_id
    )
    impact = _classify_impact(active_count, deleted_count - 1)
    if impact is not PurgeImpact.SOLE:
        msg = (
            f"Archivdatei {file_id} ist nicht SOLE (aktive oder andere "
            "gelöscht-markierte Referenzen auf denselben Inhalt vorhanden) "
            "und kann über die Konsole nicht wiederhergestellt werden."
        )
        raise ArchiveMaintenanceError(msg)

    file_obj.deleted_at = None
    db.commit()
    path, name, extension = _describe_location(db, file_obj)
    return FileLocation(
        file_id=file_id, path=path, name=name, extension=extension, deleted=False
    )


def _active_duplicates_for_store_items(
    db: Session,
    store_item_ids: set[uuid.UUID],
    exclude_file_ids: set[int] | None = None,
) -> dict[uuid.UUID, list[ActiveDuplicate]]:
    """Maps store_item_id -> its active (non-deleted) ArchiveFile
    duplicates. A single query regardless of how many store items are
    involved, so inspecting an entire directory's files doesn't turn into
    one query per file — same batching principle as _reference_counts().
    """
    if not store_item_ids:
        return {}
    result: dict[uuid.UUID, list[ActiveDuplicate]] = {sid: [] for sid in store_item_ids}
    query = db.query(ArchiveFile).filter(
        ArchiveFile.archive_store_item_id.in_(store_item_ids),
        ArchiveFile.deleted_at.is_(None),
    )
    if exclude_file_ids:
        query = query.filter(ArchiveFile.id.notin_(exclude_file_ids))
    for file_obj in query.all():
        path, name, extension = _describe_location(db, file_obj)
        result[file_obj.archive_store_item_id].append(
            ActiveDuplicate(
                file_id=file_obj.id, path=path, name=name, extension=extension
            )
        )
    return result


def active_duplicates_of_file(
    db: Session, file_id: int
) -> tuple[FileLocation, list[ActiveDuplicate]]:
    """Returns the given file's own location plus the active files sharing
    its content (any status for the given file itself — active or
    soft-deleted; `--file` is an explicit operator choice, not restricted to
    purge candidates). Raises ArchiveMaintenanceError if file_id doesn't
    exist at all — an empty duplicates list (no active duplicates) is a
    normal, non-error outcome.
    """
    file_obj = _get_file(db, file_id)
    path, name, extension = _describe_location(db, file_obj)
    location = FileLocation(
        file_id=file_id,
        path=path,
        name=name,
        extension=extension,
        deleted=file_obj.deleted_at is not None,
    )
    duplicates = _active_duplicates_for_store_items(
        db, {file_obj.archive_store_item_id}, exclude_file_ids={file_id}
    )
    return location, duplicates[file_obj.archive_store_item_id]


def active_duplicates_in_dir(
    db: Session, dir_id: int
) -> list[tuple[PurgeCandidate, list[ActiveDuplicate]]]:
    """Pairs every currently soft-deleted file directly in `dir_id` with its
    list of active duplicates. Empty list if the directory has no
    soft-deleted files (not an error). Reuses list_deleted_files_in_dir()
    for the source files (path/filename already resolved there) instead of
    querying ArchiveFile a second time.
    """
    candidates = list_deleted_files_in_dir(db, dir_id)
    if not candidates:
        return []
    store_item_ids = {c.archive_store_item_id for c in candidates}
    by_store_item = _active_duplicates_for_store_items(db, store_item_ids)
    return [(c, by_store_item[c.archive_store_item_id]) for c in candidates]


def find_file_location(db: Session, file_id: int) -> FileLocation | None:
    """Returns the file's location (any status) if a file with this id
    exists, else None. Used by the `urlpath` convenience lookup, where "not
    a file" just means the id might be a directory instead — not an error.
    """
    file_obj = db.get(ArchiveFile, file_id)
    if file_obj is None:
        return None
    path, name, extension = _describe_location(db, file_obj)
    return FileLocation(
        file_id=file_id,
        path=path,
        name=name,
        extension=extension,
        deleted=file_obj.deleted_at is not None,
    )


def find_dir_location(db: Session, dir_id: int) -> DirLocation | None:
    """Returns the directory's own resolved path/status if a dir with this
    id exists, else None. Used by the `urlpath` convenience lookup, where
    "not a directory" just means the id might be a file instead — not an
    error.
    """
    dir_obj = db.get(ArchiveDir, dir_id)
    if dir_obj is None:
        return None
    return DirLocation(
        dir_id=dir_id,
        path=dir_path_string(db, dir_obj),
        deleted=dir_obj.deleted_at is not None,
    )
