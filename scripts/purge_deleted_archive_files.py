#!/usr/bin/env python3
"""Hard-delete soft-deleted archive files from DB and S3 (destructive).

This is the ONLY way to permanently remove an archive file's data — the
regular API only ever soft-deletes (see app/services/archive_service.py).
Deliberately CLI-only: this functionality must never be exposed via the
API/frontend (see app/services/archive_purge_service.py's module docstring).

Without a subcommand, prints a short usage text — nothing is read from the
database and nothing is deleted. `list` shows every currently soft-deleted
archive file (id, deletion time, size, content hash, S3 impact, path,
description, uploader). No retention/grace period: every currently
soft-deleted file appears in that list.

`archive_store_item_id` is not unique on `ArchiveFile` — several files
(active and/or soft-deleted) can reference the same underlying S3 object
(content dedup inherited from the legacy migration). Every listing therefore
also classifies each file's S3 impact:
  - duplicate: an active file still references the same content — purging
    this file never touches S3.
  - shared: no active file, but another soft-deleted file still references
    the same content — S3 stays untouched for now, but the last remaining
    reference in the group will remove it.
  - sole (SOLE in the listing): no other reference at all — purging this
    file deletes the underlying S3 object immediately.

Permanently deleting a single file is `purge <id>`. The given id must be
present in the `list` output above (i.e. actually purgeable) — anything else
is rejected before any prompt is shown, and nothing is deleted. Only after
that check passes does the script ask for interactive confirmation; there is
no flag to skip it. The underlying S3 object (and any cached thumbnails) is
only removed once no other file version anywhere still references the same
content hash — see app/services/archive_purge_service.py::purge_file() for
the full reference-counting logic.

`purge-duplicates <dir_id>` cleans up one directory's soft-deleted files in a
single guided session (direct children only, not recursive): every
"duplicate" file is batch-purged after one confirmation (each is freshly
re-checked immediately before its own purge, so a concurrent soft-delete of
its active sibling during the batch can never turn into an unexpected S3
deletion — it is skipped and reported instead); any remaining "shared"/"sole"
files in that directory are then walked through individually, reusing the
same confirmation as `purge <id>` — declining one only skips that file, it
does not abort the rest of the walkthrough. Each file's impact note is
recomputed right before its own prompt, so purging an earlier "shared"
sibling in the same walkthrough correctly turns a later one's note into
"sole" instead of showing a stale count.

Runs in every environment, including production — unlike downsync_prod.py,
this is not dev-only tooling but the actual production cleanup mechanism.

Usage:
    python scripts/purge_deleted_archive_files.py
    python scripts/purge_deleted_archive_files.py list
    python scripts/purge_deleted_archive_files.py purge 42
    python scripts/purge_deleted_archive_files.py purge-duplicates 7
"""

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

import app.db.base  # noqa: F401 — registers all models  # pyright: ignore[reportUnusedImport]
from app.core.storage import StorageClient, get_storage
from app.db.database import SessionLocal
from app.services.archive_purge_service import (
    PurgeCandidate,
    PurgeError,
    PurgeImpact,
    is_still_duplicate,
    list_deleted_files,
    list_deleted_files_in_dir,
    purge_file,
    refresh_candidate,
)

_IMPACT_LABELS = {
    PurgeImpact.DUPLICATE: "duplicate",
    PurgeImpact.SHARED: "shared",
    PurgeImpact.SOLE: "SOLE",
}


def _human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def _print_impact_summary(candidates: list[PurgeCandidate]) -> None:
    counts = Counter(c.impact for c in candidates)
    print(
        f"  {counts[PurgeImpact.DUPLICATE]} duplicate (active copy exists — "
        f"S3 safe), {counts[PurgeImpact.SHARED]} shared (other deleted "
        f"copies exist — S3 safe for now), {counts[PurgeImpact.SOLE]} sole "
        "reference (purge deletes the S3 object)."
    )


def _print_candidates(candidates: list[PurgeCandidate]) -> None:
    if not candidates:
        print("No soft-deleted archive files found.")
        return

    print(
        f"{'ID':<6} {'DELETED_AT':<20} {'SIZE':>9}  {'HASH':<16}  "
        f"{'IMPACT':<10} {'PATH':<20} {'FILENAME':<24} {'DESCRIPTION':<22} "
        "CREATED_BY"
    )
    total_size = 0
    for c in candidates:
        deleted_at = c.deleted_at.strftime("%Y-%m-%d %H:%M:%S")
        hash_short = (c.sha256_hash or "")[:16]
        total_size += c.size
        print(
            f"{c.file_id:<6} {deleted_at:<20} {_human_size(c.size):>9}  "
            f"{hash_short:<16}  {_IMPACT_LABELS[c.impact]:<10} "
            f"{c.path:<20} {c.filename:<24} "
            f"{(c.description or ''):<22} {c.created_by or ''}"
        )
    print(
        f"\n{len(candidates)} soft-deleted file(s). "
        f"Total size: {_human_size(total_size)}."
    )
    if len(candidates) > 1:
        _print_impact_summary(candidates)


def _print_impact_note(candidate: PurgeCandidate) -> None:
    if candidate.impact is PurgeImpact.DUPLICATE:
        print(
            f"Note: {candidate.active_sibling_count} active file(s) still "
            "reference this content — the S3 object will NOT be deleted."
        )
    elif candidate.impact is PurgeImpact.SHARED:
        print(
            f"Note: {candidate.other_deleted_sibling_count} other "
            "soft-deleted file(s) still reference this content — the S3 "
            "object will NOT be deleted yet; it is removed once the last "
            "referencing file is purged."
        )
    else:
        print(
            "WARNING: this is the ONLY reference to this content — purging "
            "PERMANENTLY DELETES the underlying S3 object and its cached "
            "thumbnails."
        )


def _ask_yes(prompt: str) -> bool:
    return input(prompt).strip().lower() == "yes"


def _confirm(candidate: PurgeCandidate) -> None:
    _print_impact_note(candidate)
    prompt = (
        f'Type "yes" to permanently delete file {candidate.file_id} '
        f'("{candidate.filename}" in "{candidate.path}") from DB and S3: '
    )
    if not _ask_yes(prompt):
        print("Aborted.")
        sys.exit(0)


def _purge_one(
    db: Session,
    storage: StorageClient,
    candidate: PurgeCandidate,
    *,
    expect_no_s3_impact: bool = False,
) -> bool:
    """Purges the given candidate. Returns True if an error occurred."""
    try:
        result = purge_file(db, storage, candidate.file_id)
    except PurgeError as exc:
        print(f"ERROR: file {candidate.file_id}: {exc}", file=sys.stderr)
        return True

    print(
        f"Purged file {candidate.file_id} "
        f'("{candidate.filename}" in "{candidate.path}").'
    )
    had_error = False
    if expect_no_s3_impact and result.store_item_deleted:
        print(
            f"  WARNING: file {candidate.file_id}'s S3 object was deleted "
            "unexpectedly — its active sibling must have been removed "
            "between the live re-check and this purge.",
            file=sys.stderr,
        )
        had_error = True
    for err in result.s3_errors:
        print(f"  WARNING: S3 cleanup failed: {err}", file=sys.stderr)
        had_error = True
    return had_error


def _purge_duplicates_batch(
    db: Session, storage: StorageClient, duplicates: list[PurgeCandidate]
) -> tuple[int, int, bool]:
    """Purges every candidate in `duplicates` after a single batch
    confirmation. Each one is freshly re-checked immediately before its own
    purge — a concurrent soft-delete of its active sibling between listing
    and this batch running must not silently turn a "no S3 risk" item into
    an actual S3 deletion; it is skipped and reported instead.

    Returns (purged_count, skipped_count, had_error).
    """
    total_size = sum(c.size for c in duplicates)
    prompt = (
        f'Type "yes" to permanently delete these {len(duplicates)} '
        f"duplicate file(s) ({_human_size(total_size)}) from DB — S3 "
        "objects are not affected: "
    )
    if not _ask_yes(prompt):
        print("Duplicate batch skipped.")
        return 0, 0, False

    purged = 0
    skipped = 0
    had_error = False
    for candidate in duplicates:
        if not is_still_duplicate(db, candidate):
            print(
                f"WARNING: file {candidate.file_id} skipped — no longer a "
                "safe duplicate (its active sibling is gone); review it "
                "individually via 'purge <id>'.",
                file=sys.stderr,
            )
            skipped += 1
            continue
        if _purge_one(db, storage, candidate, expect_no_s3_impact=True):
            had_error = True
        else:
            purged += 1
    return purged, skipped, had_error


def _walkthrough_remainder(
    db: Session, storage: StorageClient, remainder: list[PurgeCandidate]
) -> tuple[int, int, bool]:
    """Walks the operator through each non-duplicate candidate individually.
    Declining one (anything other than "yes") only skips that file and moves
    on — unlike the top-level `purge <id>` command, it does not abort the
    rest of the walkthrough.

    Each candidate's impact is freshly re-checked right before it is shown:
    the `remainder` list was computed once up front, so purging one
    "shared" file earlier in this same walkthrough can turn a later sibling
    into the last remaining reference — without the refresh, that later
    file would still display a stale "N other file(s) still reference
    this" note even though it is actually about to become a real S3
    deletion.

    Returns (purged_count, declined_count, had_error).
    """
    print(
        f"\nThe following {len(remainder)} file(s) need individual review "
        "(would affect S3):"
    )
    purged = 0
    declined = 0
    had_error = False
    for candidate in remainder:
        live_candidate = refresh_candidate(db, candidate)
        _print_candidates([live_candidate])
        _print_impact_note(live_candidate)
        prompt = (
            f'Type "yes" to permanently delete file {live_candidate.file_id} '
            f'("{live_candidate.filename}" in "{live_candidate.path}") from '
            "DB and S3, or anything else to skip it: "
        )
        if not _ask_yes(prompt):
            declined += 1
            continue
        if _purge_one(db, storage, live_candidate):
            had_error = True
        else:
            purged += 1
    return purged, declined, had_error


def _run_purge_duplicates(db: Session, dir_id: int) -> NoReturn:
    candidates = list_deleted_files_in_dir(db, dir_id)
    if not candidates:
        print("No soft-deleted files in this directory.")
        sys.exit(0)

    _print_candidates(candidates)

    duplicates = [c for c in candidates if c.impact is PurgeImpact.DUPLICATE]
    remainder = [c for c in candidates if c.impact is not PurgeImpact.DUPLICATE]

    storage = get_storage()
    purged_total = 0
    skipped_total = 0
    had_error = False

    if duplicates:
        purged, skipped, batch_error = _purge_duplicates_batch(db, storage, duplicates)
        purged_total += purged
        skipped_total += skipped
        had_error = had_error or batch_error

    if remainder:
        purged, declined, walkthrough_error = _walkthrough_remainder(
            db, storage, remainder
        )
        purged_total += purged
        skipped_total += declined
        had_error = had_error or walkthrough_error

    print(f"\n{purged_total} file(s) purged, {skipped_total} skipped.")
    sys.exit(1 if had_error else 0)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List or permanently purge soft-deleted archive files (DB + S3).",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "list", help="List every currently soft-deleted (purgeable) archive file"
    )
    purge_parser = subparsers.add_parser(
        "purge",
        help="Permanently delete one file (after confirmation); ID must appear in list",
    )
    purge_parser.add_argument(
        "id", type=int, help="ID of the soft-deleted file to purge"
    )
    purge_duplicates_parser = subparsers.add_parser(
        "purge-duplicates",
        help=(
            "Batch-purge safely duplicated (S3-unaffected) files in one "
            "directory, then walk through the rest individually"
        ),
    )
    purge_duplicates_parser.add_argument(
        "dir_id",
        type=int,
        help="archive_dir_id to clean up (direct children only, not recursive)",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    db = SessionLocal()
    try:
        if args.command == "purge-duplicates":
            _run_purge_duplicates(db, args.dir_id)

        candidates = list_deleted_files(db)

        if args.command == "list":
            _print_candidates(candidates)
            sys.exit(0)

        target = next((c for c in candidates if c.file_id == args.id), None)
        if target is None:
            print(
                f"ERROR: file {args.id} is not currently soft-deleted, not purgeable.",
                file=sys.stderr,
            )
            sys.exit(1)

        _print_candidates([target])
        _confirm(target)

        storage = get_storage()
        had_error = _purge_one(db, storage, target)
        sys.exit(1 if had_error else 0)
    finally:
        db.close()


if __name__ == "__main__":
    main()
