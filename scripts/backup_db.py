#!/usr/bin/env python3
"""CLI script to manually trigger a PostgreSQL database backup to S3.

Usage:
    python scripts/backup_db.py [--list] [--cleanup]

Options:
    --list      Print available backups and exit (no backup is created).
    --cleanup   Also delete backups older than BACKUP_RETENTION_DAYS after
                the new backup succeeds (same cleanup the scheduled job
                runs; opt-in here so a manual backup never deletes other
                backups as a side effect unless explicitly requested).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.storage import S3_PATH_DB_BACKUPS, StorageClient, get_storage
from app.services.backup_service import cleanup_old_backups, run_backup


def _print_backup_list(storage: StorageClient) -> None:
    keys = storage.list_keys(prefix=f"{S3_PATH_DB_BACKUPS}/")
    if not keys:
        print("No backups found.", file=sys.stderr)
        return
    for key in sorted(keys):
        print(key)


def _run_cleanup(storage: StorageClient) -> None:
    deleted = cleanup_old_backups(storage)
    if not deleted:
        print("No expired backups to clean up.")
        return
    print(f"Cleaned up {len(deleted)} expired backup(s):")
    for name in deleted:
        print(f"  {name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manually trigger a PostgreSQL DB backup to S3.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available backups and exit, without creating one.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Also delete backups older than BACKUP_RETENTION_DAYS afterward.",
    )
    args = parser.parse_args()

    storage = get_storage()

    if args.list:
        _print_backup_list(storage)
        sys.exit(0)

    try:
        backup_name = run_backup(storage, manual=True)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Backup complete: {backup_name}")

    if args.cleanup:
        _run_cleanup(storage)


if __name__ == "__main__":
    main()
