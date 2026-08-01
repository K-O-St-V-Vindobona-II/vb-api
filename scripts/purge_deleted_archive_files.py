#!/usr/bin/env python3
"""Hard-delete a single soft-deleted archive file from DB and S3 (destructive).

This is the ONLY way to permanently remove an archive file's data — the
regular API only ever soft-deletes (see app/services/archive_service.py).
Deliberately CLI-only: this functionality must never be exposed via the
API/frontend (see app/services/archive_purge_service.py's module docstring).

Without a subcommand, prints a short usage text — nothing is read from the
database and nothing is deleted. `list` shows every currently soft-deleted
archive file (id, deletion time, size, content hash, path, description,
uploader). No retention/grace period: every currently soft-deleted file
appears in that list.

Permanently deleting a file is `purge <id>`. The given id must be present
in the `list` output above (i.e. actually purgeable) — anything else is
rejected before any prompt is shown, and nothing is deleted. Only after
that check passes does the script ask for interactive confirmation; there
is no flag to skip it. The underlying S3 object (and any cached
thumbnails) is only removed once no other file version anywhere still
references the same content hash — see
app/services/archive_purge_service.py::purge_file() for the full
reference-counting logic.

Runs in every environment, including production — unlike downsync_prod.py,
this is not dev-only tooling but the actual production cleanup mechanism.

Usage:
    python scripts/purge_deleted_archive_files.py
    python scripts/purge_deleted_archive_files.py list
    python scripts/purge_deleted_archive_files.py purge 42
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
        f"{'PATH':<20} {'FILENAME':<24} {'DESCRIPTION':<22} CREATED_BY"
    )
    total_size = 0
    for c in candidates:
        deleted_at = c.deleted_at.strftime("%Y-%m-%d %H:%M:%S")
        hash_short = (c.sha256_hash or "")[:16]
        total_size += c.size
        print(
            f"{c.file_id:<6} {deleted_at:<20} {_human_size(c.size):>9}  "
            f"{hash_short:<16}  {c.path:<20} {c.filename:<24} "
            f"{(c.description or ''):<22} {c.created_by or ''}"
        )
    print(
        f"\n{len(candidates)} soft-deleted file(s). "
        f"Total size: {_human_size(total_size)}."
    )


def _confirm(candidate: PurgeCandidate) -> None:
    prompt = (
        f'Type "yes" to permanently delete file {candidate.file_id} '
        f'("{candidate.filename}" in "{candidate.path}") from DB and S3: '
    )
    answer = input(prompt)
    if answer.strip().lower() != "yes":
        print("Aborted.")
        sys.exit(0)


def _purge_one(db: Session, storage: StorageClient, candidate: PurgeCandidate) -> bool:
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
    if not result.s3_errors:
        return False
    for err in result.s3_errors:
        print(f"  WARNING: S3 cleanup failed: {err}", file=sys.stderr)
    return True


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
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    db = SessionLocal()
    try:
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
