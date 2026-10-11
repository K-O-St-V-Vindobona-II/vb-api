"""Hard-deletion of an archive file row together with the stored content that
only it referenced.

Shared by the CLI maintenance of soft-deleted files and by a member deleting
their own unfiled upload. It imports neither archive_service nor
archive_maintenance_service, so both can use it without a circular import.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from app.core.storage import S3_PATH_ARCHIVE_CACHE, S3_PATH_ARCHIVE_STORE, StorageClient
from app.models.archive_file import ArchiveFile
from app.models.archive_store_item import ArchiveStoreItem

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class HardDeleteOutcome:
    store_item_deleted: bool
    s3_keys_deleted: list[str]
    s3_errors: list[str]


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


def hard_delete_file(
    db: Session, storage: StorageClient, file_obj: ArchiveFile
) -> HardDeleteOutcome:
    """Permanently deletes an archive file: hard-deletes the DB row (cascading
    its comments), then removes the underlying S3 object(s) if no other file
    still references the same content hash.

    DB changes are committed BEFORE any S3 deletion is attempted: if the S3
    step later fails, the worst case is an orphaned S3 object — a benign,
    self-diagnosable state already detected by check_s3_integrity.py. The
    reverse order risks a DB row surviving that wrongly believed an S3
    object it still needed was removed — silent data loss for other files
    that may still reference the same content hash.

    Whether the caller may delete this file at all is not decided here.
    """
    # Snapshot the store item before deleting. archive_store_item_id has no
    # unique constraint on ArchiveFile and can be shared across files (real
    # content dedup from the initial data import), so the reference count must
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

    return HardDeleteOutcome(
        store_item_deleted=bool(orphaned_items),
        s3_keys_deleted=s3_keys_deleted,
        s3_errors=s3_errors,
    )
