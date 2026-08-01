#!/usr/bin/env python3
"""Hard-delete soft-deleted archive files from DB and S3 (destructive).

This is the ONLY way to permanently remove an archive file's data — the
regular API only ever soft-deletes (see app/services/archive_service.py).
Deliberately CLI-only: this functionality must never be exposed via the
API/frontend (see app/services/archive_purge_service.py's module docstring).

Lists every currently soft-deleted archive file (id, deletion time, size,
content hash, path, description, uploader) and, after confirmation,
hard-deletes each one from the database. The underlying S3 object (and any
cached thumbnails) is only removed once no other file version anywhere
still references the same content hash — see
app/services/archive_purge_service.py::purge_file() for the full
reference-counting logic.

No retention/grace period — every currently soft-deleted file is a
candidate. The listing (with each file's deleted_at) is shown before the
confirmation prompt so the operator can judge for themselves. Runs in every
environment, including production — unlike downsync_prod.py, this is not
dev-only tooling but the actual production cleanup mechanism.

Usage:
    python scripts/purge_deleted_archive_files.py --list
    python scripts/purge_deleted_archive_files.py --dry-run
    python scripts/purge_deleted_archive_files.py
    python scripts/purge_deleted_archive_files.py --yes
    python scripts/purge_deleted_archive_files.py --file-id 12 --file-id 34
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

import app.db.base  # noqa: F401 — registers all models  # pyright: ignore[reportUnusedImport]
from app.core.storage import StorageClient, get_storage
from app.db.database import SessionLocal
from app.services.archive_purge_service import (
    PurgeCandidate,
    PurgeError,
    list_deleted_files,
    purge_file,
)


def _human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def _print_candidates(candidates: list[PurgeCandidate]) -> None:
    if not candidates:
        print("No soft-deleted archive files found.")
        return

    print(
        f"{'ID':<6} {'DELETED_AT':<20} {'SIZE':>9}  {'HASH':<16}  "
        f"{'PATH':<28} {'DESCRIPTION':<22} CREATED_BY"
    )
    total_size = 0
    for c in candidates:
        deleted_at = c.deleted_at.strftime("%Y-%m-%d %H:%M:%S")
        hash_short = (c.sha256_hash or "")[:16]
        total_size += c.size
        print(
            f"{c.file_id:<6} {deleted_at:<20} {_human_size(c.size):>9}  "
            f"{hash_short:<16}  {c.path:<28} {(c.description or ''):<22} "
            f"{c.created_by or ''}"
        )
    print(
        f"\n{len(candidates)} soft-deleted file(s). "
        f"Total size: {_human_size(total_size)}."
    )


def _resolve_targets(
    candidates: list[PurgeCandidate],
    file_ids: list[int] | None,
) -> tuple[list[PurgeCandidate], list[int]]:
    """Filters candidates to --file-id, if given. Returns (targets,
    missing_ids) — missing_ids are requested IDs that are not currently
    soft-deleted (typo, or restored between listing and this run)."""
    if not file_ids:
        return candidates, []
    by_id = {c.file_id: c for c in candidates}
    targets = [by_id[fid] for fid in file_ids if fid in by_id]
    missing = [fid for fid in file_ids if fid not in by_id]
    return targets, missing


def _warn_missing(missing_ids: list[int]) -> None:
    for fid in missing_ids:
        print(
            f"WARNING: file {fid} is not currently soft-deleted, skipping.",
            file=sys.stderr,
        )


def _print_dry_run_notice(targets: list[PurgeCandidate]) -> None:
    print(f"\n{len(targets)} file(s) WOULD be permanently deleted (dry run):")
    for c in targets:
        print(f"  WOULD PURGE: file {c.file_id} ({c.path})")


def _confirm(count: int, auto_yes: bool) -> None:
    if auto_yes:
        return
    prompt = f'Type "yes" to permanently delete {count} file(s) from DB and S3: '
    answer = input(prompt)
    if answer.strip().lower() != "yes":
        print("Aborted.")
        sys.exit(0)


def _run_purge_batch(
    db: Session,
    storage: StorageClient,
    targets: list[PurgeCandidate],
) -> bool:
    """Purges every target, continuing past a single failed file rather
    than aborting the whole batch. Returns True if any error occurred."""
    had_errors = False
    for candidate in targets:
        try:
            result = purge_file(db, storage, candidate.file_id)
        except PurgeError as exc:
            print(f"ERROR: file {candidate.file_id}: {exc}", file=sys.stderr)
            had_errors = True
            continue
        print(f"Purged file {candidate.file_id} ({candidate.path}).")
        if result.s3_errors:
            had_errors = True
            for err in result.s3_errors:
                print(f"  WARNING: S3 cleanup failed: {err}", file=sys.stderr)
    return had_errors


def _exit_code(*, had_issues: bool) -> int:
    return 1 if had_issues else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List and permanently delete (DB + S3) soft-deleted archive files",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Only list soft-deleted files, do not prompt or delete anything",
    )
    parser.add_argument(
        "--file-id",
        action="append",
        type=int,
        dest="file_id",
        help="Restrict the operation to specific file ID(s) (repeatable)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be permanently deleted - do not execute",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    db = SessionLocal()
    try:
        candidates = list_deleted_files(db)
        targets, missing_ids = _resolve_targets(candidates, args.file_id)
        _warn_missing(missing_ids)
        _print_candidates(targets)

        if not targets:
            sys.exit(_exit_code(had_issues=bool(missing_ids)))

        if args.list or args.dry_run:
            if args.dry_run:
                _print_dry_run_notice(targets)
            sys.exit(_exit_code(had_issues=bool(missing_ids)))

        _confirm(len(targets), args.yes)
        storage = get_storage()
        had_errors = _run_purge_batch(db, storage, targets)
        sys.exit(_exit_code(had_issues=had_errors or bool(missing_ids)))
    finally:
        db.close()


if __name__ == "__main__":
    main()
