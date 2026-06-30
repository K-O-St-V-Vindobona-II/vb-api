#!/usr/bin/env python3
"""CLI script to restore the PostgreSQL database from an S3 backup.

Usage:
    python scripts/restore_db.py [--list] [--backup-name NAME] [--force]

Options:
    --list          Print available backups and exit.
    --backup-name   Specific backup filename to restore (default: latest).
    --force         Required when APP_ENVIRONMENT=production.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.storage import S3_PATH_DB_BACKUPS, get_storage
from app.services.backup_service import run_restore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restore the PostgreSQL DB from an S3 backup.",
    )
    parser.add_argument(
        "--backup-name",
        metavar="NAME",
        help="Backup filename to restore (default: latest).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Required when APP_ENVIRONMENT=production.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available backups and exit.",
    )
    args = parser.parse_args()

    storage = get_storage()

    if args.list:
        keys = storage.list_keys(prefix=f"{S3_PATH_DB_BACKUPS}/")
        if not keys:
            print("No backups found.", file=sys.stderr)
            sys.exit(0)
        for key in sorted(keys):
            print(key)
        sys.exit(0)

    try:
        run_restore(storage, backup_name=args.backup_name, force=args.force)
        print("Restore complete.")
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
