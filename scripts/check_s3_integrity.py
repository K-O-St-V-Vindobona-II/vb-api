#!/usr/bin/env python3
"""CLI wrapper around app.services.storage_integrity_service.

Checks, for both the Archive store and the Standesdb image store:
1. Every sha256_hash referenced in the DB exists in S3 (exit 1 if anything
   is missing).
2. S3 objects under the store prefixes referenced by NO database row at
   all (active or soft-deleted) — for manual review.

This script NEVER deletes anything. Cleanup, if desired, must be done
manually via the S3 web console.

Exit codes: 0 = nothing missing, 1 = at least one file is missing, 2 = the
check itself could not finish (database or storage error), so that a caller
never mistakes a broken run for missing files.

Usage:
    python scripts/check_s3_integrity.py
"""

import sys
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.exc import SQLAlchemyError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.db.base  # noqa: F401 — registers all models  # pyright: ignore[reportUnusedImport]
from app.core.datetime_utils import get_app_timezone
from app.core.storage import StorageClient, get_storage
from app.db.database import SessionLocal
from app.services.storage_integrity_service import (
    IntegrityReport,
    check_archive_integrity,
    check_standesdb_integrity,
)

EXIT_MISSING = 1
EXIT_CHECK_FAILED = 2


def _human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def _print_orphan_details(storage: StorageClient, orphans: list[str]) -> None:
    print(f"{'KEY':<60} {'SIZE':>10} {'CONTENT-TYPE':<30} LAST-MODIFIED")
    for key in orphans:
        try:
            meta = storage.head(key)
        except ClientError:
            print(f"{key:<60} (removed or unreadable since it was listed)")
            continue
        size = _human_size(meta["size"])
        last_modified = (
            meta["last_modified"]
            .astimezone(get_app_timezone())
            .strftime("%Y-%m-%d %H:%M %Z")
        )
        print(f"{key:<60} {size:>10} {meta['content_type']:<30} {last_modified}")


def print_report(name: str, storage: StorageClient, report: IntegrityReport) -> None:
    print(f"\n=== {name} ===")
    print(f"{len(report.missing)} missing file(s).")
    for key in report.missing:
        print(f"  MISSING: {key}")

    if not report.orphans:
        print("No orphaned files found.")
        return
    print(f"\n{len(report.orphans)} orphaned file(s) — referenced by NO database row:")
    _print_orphan_details(storage, report.orphans)
    print(
        "\nNOTE: This is an information-only report. Deletion (if desired) "
        "must be performed manually via the S3 web console — never by script."
    )


def _run_checks() -> int:
    storage = get_storage()
    db = SessionLocal()
    try:
        archive_report = check_archive_integrity(db, storage)
        standesdb_report = check_standesdb_integrity(db, storage)
    finally:
        db.close()

    print_report("Archive", storage, archive_report)
    print_report("Standesdb", storage, standesdb_report)

    missing_total = len(archive_report.missing) + len(standesdb_report.missing)
    return EXIT_MISSING if missing_total else 0


def main() -> None:
    try:
        exit_code = _run_checks()
    except (BotoCoreError, ClientError, SQLAlchemyError) as exc:
        print(f"ERROR: the integrity check could not finish: {exc}", file=sys.stderr)
        sys.exit(EXIT_CHECK_FAILED)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
