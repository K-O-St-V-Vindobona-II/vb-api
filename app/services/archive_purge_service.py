"""Hard-delete (DB + S3) for soft-deleted archive files.

CLI-only, deliberately. This module must never be imported from anything
under app/api/ — the only entry point is
scripts/purge_deleted_archive_files.py, run manually by an operator after
reviewing the listing. There is no HTTP endpoint for this and there must
never be one.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum, auto
from typing import cast

from botocore.exceptions import ClientError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.storage import S3_PATH_ARCHIVE_CACHE, S3_PATH_ARCHIVE_STORE, StorageClient
from app.models.archive_file import ArchiveFile
from app.models.archive_store_item import ArchiveStoreItem
from app.services.archive_service import dir_path_string


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


@dataclass(frozen=True)
class PurgeCandidate:
    file_id: int
    path: str
    filename: str
    description: str | None
    deleted_at: datetime
    size: int
    sha256_hash: str
    created_by: str | None
    archive_store_item_id: int = 0
    active_sibling_count: int = 0
    other_deleted_sibling_count: int = 0

    @property
    def impact(self) -> PurgeImpact:
        if self.active_sibling_count > 0:
            return PurgeImpact.DUPLICATE
        if self.other_deleted_sibling_count > 0:
            return PurgeImpact.SHARED
        return PurgeImpact.SOLE


@dataclass(frozen=True)
class PurgeResult:
    file_id: int
    store_item_deleted: bool
    s3_keys_deleted: list[str]
    s3_errors: list[str]


class PurgeError(RuntimeError):
    """Raised when a file cannot be safely hard-deleted (not found, or not
    currently soft-deleted)."""


def _reference_counts(
    db: Session, store_item_ids: set[int]
) -> dict[int, tuple[int, int]]:
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
    item = file_obj.store_item
    path = (
        dir_path_string(db, file_obj.archive_dir)
        if file_obj.archive_dir is not None
        else "Archiv"
    )
    return PurgeCandidate(
        file_id=file_obj.id,
        path=path,
        filename=f"{item.name}.{item.extension}",
        description=file_obj.description,
        deleted_at=cast("datetime", file_obj.deleted_at),
        size=item.size,
        sha256_hash=item.sha256_hash,
        created_by=item.member.cn if item.member else None,
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


def _live_sibling_counts(db: Session, store_item_id: int) -> tuple[int, int]:
    """Freshly (re-)computes (active_count, deleted_count) for a single
    store item, including the candidate's own row. Shared by
    `is_still_duplicate()` and `refresh_candidate()`, both of which need an
    up-to-date count for exactly one candidate rather than a whole listing.
    """
    return _reference_counts(db, {store_item_id})[store_item_id]


def refresh_candidate(db: Session, candidate: PurgeCandidate) -> PurgeCandidate:
    """Returns a copy of `candidate` with freshly recomputed sibling counts
    (and thus `impact`). Listings are computed once up front, so within a
    single multi-file session (e.g. the purge-duplicates walkthrough)
    purging one file can change what an *already listed* sibling's impact
    note should say — without this, a later file could still display a
    stale "N other file(s) still reference this" note after those other
    files were already purged earlier in the same run.
    """
    active_count, deleted_count = _live_sibling_counts(
        db, candidate.archive_store_item_id
    )
    return replace(
        candidate,
        active_sibling_count=active_count,
        other_deleted_sibling_count=deleted_count - 1,
    )


def is_still_duplicate(db: Session, candidate: PurgeCandidate) -> bool:
    """Freshly re-checks, right before an actual batch purge, whether the
    candidate still has an active sibling referencing the same content. Used
    only on the batch (duplicate-only) purge path — a concurrent soft-delete
    of that active sibling between listing and purging must not silently
    turn a "no S3 risk" batch item into one that deletes the S3 object.
    """
    active_count, _ = _live_sibling_counts(db, candidate.archive_store_item_id)
    return active_count > 0


def _validate_purge_target(file_obj: ArchiveFile | None, file_id: int) -> ArchiveFile:
    if file_obj is None:
        msg = f"Archivdatei {file_id} nicht gefunden."
        raise PurgeError(msg)
    if file_obj.deleted_at is None:
        msg = f"Archivdatei {file_id} ist nicht gelöscht-markiert."
        raise PurgeError(msg)
    return file_obj


def _delete_orphaned_store_items(
    db: Session, store_item_ids: set[int]
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
        item = db.get(ArchiveStoreItem, store_item_id)
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
    file_obj = _validate_purge_target(db.get(ArchiveFile, file_id), file_id)

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
