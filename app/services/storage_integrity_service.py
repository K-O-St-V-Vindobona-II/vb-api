"""Read-only integrity checks between DB references and S3 objects.

Shared by scripts/check_s3_integrity.py (manual CLI) and the weekly
archive/standesdb health-check scheduler jobs (app/core/scheduler.py).

This never deletes anything - cleanup of orphaned S3 objects, if desired,
must be done manually via the S3 web console.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.storage import (
    S3_PATH_ARCHIVE_STORE,
    S3_PATH_STANDESDB_IMAGES,
    StorageClient,
)
from app.models.archive_store_item import ArchiveStoreItem
from app.models.standesdb_image import StandesdbImage


@dataclass
class IntegrityReport:
    """missing: DB-referenced sha256 hashes absent from S3.

    orphans: S3 objects referenced by NO database row (active or
    soft-deleted).
    """

    missing: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        return not self.missing


def _check_integrity(
    db: Session,
    storage: StorageClient,
    prefix: str,
    hash_column: InstrumentedAttribute[str],
) -> IntegrityReport:
    """Compare DB-referenced hashes against S3 objects under `prefix`.

    A single bulk list_keys() call plus in-memory set operations, not a
    head_object() call per DB row - with tens of thousands of rows, per-row
    HEAD requests take tens of minutes over the network (see
    tests/scripts/test_check_s3_integrity.py for the regression this guards
    against).
    """
    s3_keys = set(storage.list_keys(f"{prefix}/"))
    referenced = {f"{prefix}/{h}" for (h,) in db.query(hash_column).all()}
    return IntegrityReport(
        missing=sorted(referenced - s3_keys),
        orphans=sorted(s3_keys - referenced),
    )


def check_archive_integrity(db: Session, storage: StorageClient) -> IntegrityReport:
    return _check_integrity(
        db,
        storage,
        S3_PATH_ARCHIVE_STORE,
        ArchiveStoreItem.sha256_hash,
    )


def check_standesdb_integrity(db: Session, storage: StorageClient) -> IntegrityReport:
    return _check_integrity(
        db,
        storage,
        S3_PATH_STANDESDB_IMAGES,
        StandesdbImage.sha256_hash,
    )
