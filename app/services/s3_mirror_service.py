"""Generic prefix mirror between two S3-compatible buckets that share the
same key structure — used by scripts/downsync_prod.py to clone the
production AWS bucket into local MinIO. Unlike the retired legacy
downsync_from_prod_aws.py, no key remapping is needed here: source and
dest are expected to organize objects identically, so this is a plain
list-diff-copy-delete mirror.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.storage import StorageClient

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]  # (done, total)


@dataclass
class MirrorResult:
    """Outcome of mirroring one prefix (or the whole bucket) from source
    into dest."""

    synced: list[str] = field(default_factory=list)
    skipped: int = 0
    deleted: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


def _copy_key(source: StorageClient, dest: StorageClient, key: str) -> None:
    data, content_type = source.download_with_metadata(key)
    dest.upload(key=key, data=data, content_type=content_type)


def _delete_orphans(
    dest: StorageClient,
    orphan_keys: set[str],
    *,
    dry_run: bool,
) -> list[str]:
    deleted: list[str] = []
    for key in sorted(orphan_keys):
        if not dry_run:
            dest.delete(key)
        deleted.append(key)
    return deleted


def mirror_prefix(
    source: StorageClient,
    dest: StorageClient,
    prefix: str = "",
    *,
    dry_run: bool = False,
    delete_orphans: bool = True,
    on_progress: ProgressCallback | None = None,
) -> MirrorResult:
    """Mirror all keys under `prefix` (default: the entire bucket) from
    source into dest.

    Lists both sides once via list_keys() and diffs via set operations,
    copies keys missing on dest (content-type preserved via
    download_with_metadata(), a single S3 request per object), and
    deletes dest keys absent from source when delete_orphans=True. A
    failed copy is recorded in the result and does not abort the sync.
    dry_run performs no writes but the result still reports what would
    happen.
    """
    source_keys = set(source.list_keys(prefix=prefix))
    dest_keys = set(dest.list_keys(prefix=prefix))

    missing = sorted(source_keys - dest_keys)
    orphans = dest_keys - source_keys

    result = MirrorResult(skipped=len(source_keys) - len(missing))
    total = len(missing)
    interval = 500 if total > 2000 else 50

    for i, key in enumerate(missing, 1):
        if not dry_run:
            try:
                _copy_key(source, dest, key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Mirror copy failed for %s: %s", key, exc)
                result.errors.append(key)
                continue
        result.synced.append(key)
        if on_progress and (i % interval == 0 or i == total):
            on_progress(i, total)

    if delete_orphans:
        result.deleted = _delete_orphans(dest, orphans, dry_run=dry_run)

    return result
