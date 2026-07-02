#!/usr/bin/env python3
"""Read-only integrity check between DB references and S3 objects.

Checks:
1. Every sha256_hash referenced in StandesdbImage/ArchiveStoreItem exists
   in S3 (exit 1 if anything is missing).
2. Lists S3 objects under the store/image prefixes that are referenced by
   NO database row at all (active or soft-deleted) — for manual review.

This script NEVER deletes anything. Cleanup, if desired, must be done
manually via the S3 web console.

Usage:
    python scripts/check_s3_integrity.py
"""

import os
import sys
from pathlib import Path

import boto3
from botocore.client import BaseClient, Config
from botocore.exceptions import ClientError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.db.base  # noqa: F401 — registers all models  # pyright: ignore[reportUnusedImport]
from app.models.archive_store_item import ArchiveStoreItem
from app.models.standesdb_image import StandesdbImage

STANDESDB_PREFIX = os.environ.get("S3_PATH_STANDESDB_IMAGES", "standesdb/images")
ARCHIVE_PREFIX = os.environ.get("S3_PATH_ARCHIVE_STORE", "archive/store")


def get_s3_client() -> tuple[BaseClient, str]:
    return (
        boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
            aws_access_key_id=os.environ.get("S3_ACCESS_KEY", ""),
            aws_secret_access_key=os.environ.get("S3_SECRET_KEY", ""),
            region_name=os.environ.get("S3_REGION", "us-east-1"),
            config=Config(signature_version="s3v4"),
        ),
        os.environ.get("S3_BUCKET", "vindobona2-at"),
    )


def get_db_session() -> Session:
    db_url = os.environ.get("DATABASE_URL", "sqlite:////database/legacy_db.sqlite3")
    return sessionmaker(bind=create_engine(db_url))()


def object_exists(client: BaseClient, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
    except ClientError:
        return False
    return True


def list_prefix(client: BaseClient, bucket: str, prefix: str) -> set[str]:
    keys: set[str] = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/"):
        keys.update(obj["Key"] for obj in page.get("Contents", []))
    return keys


def check_completeness(client: BaseClient, bucket: str, db: Session) -> int:
    """Return count of missing files (DB-referenced but absent in S3)."""
    # Column-only queries, not full ORM entities: ArchiveStoreItem.member
    # uses lazy="joined", so `db.query(ArchiveStoreItem)` would eagerly
    # join and materialize the full (73-column) Member row for every one
    # of tens of thousands of items — this caused an OOM kill (~2.1GB RSS)
    # in the sibling migrate_to_s3.py, which had the same pattern.
    missing = 0
    for item_id, sha256_hash in db.query(
        StandesdbImage.id, StandesdbImage.sha256_hash
    ).all():
        key = f"{STANDESDB_PREFIX}/{sha256_hash}"
        if not object_exists(client, bucket, key):
            print(f"  MISSING: {key} (StandesdbImage.id={item_id})")
            missing += 1
    for item_id, sha256_hash in db.query(
        ArchiveStoreItem.id, ArchiveStoreItem.sha256_hash
    ).all():
        key = f"{ARCHIVE_PREFIX}/{sha256_hash}"
        if not object_exists(client, bucket, key):
            print(f"  MISSING: {key} (ArchiveStoreItem.id={item_id})")
            missing += 1
    return missing


def find_orphans(client: BaseClient, bucket: str, db: Session) -> list[str]:
    """Return S3 keys not referenced by any DB row (active or soft-deleted)."""
    referenced_standesdb = {
        f"{STANDESDB_PREFIX}/{h}"
        for (h,) in db.query(StandesdbImage.sha256_hash).distinct()
    }
    referenced_archive = {
        f"{ARCHIVE_PREFIX}/{h}"
        for (h,) in db.query(ArchiveStoreItem.sha256_hash).distinct()
    }
    s3_standesdb = list_prefix(client, bucket, STANDESDB_PREFIX)
    s3_archive = list_prefix(client, bucket, ARCHIVE_PREFIX)

    return sorted(
        (s3_standesdb - referenced_standesdb) | (s3_archive - referenced_archive)
    )


def _human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def print_orphan_report(client: BaseClient, bucket: str, orphans: list[str]) -> None:
    if not orphans:
        print("No orphaned files found.")
        return

    print(f"\n{len(orphans)} orphaned file(s) — referenced by NO database row:")
    print(f"{'KEY':<60} {'SIZE':>10} {'CONTENT-TYPE':<30} LAST-MODIFIED")
    for key in orphans:
        head = client.head_object(Bucket=bucket, Key=key)
        size = _human_size(head["ContentLength"])
        content_type = head.get("ContentType", "unknown")
        last_modified = head["LastModified"].strftime("%Y-%m-%d %H:%M")
        print(f"{key:<60} {size:>10} {content_type:<30} {last_modified}")
    print(
        "\nNOTE: This is an information-only report. Deletion (if desired) "
        "must be performed manually via the S3 web console — never by script."
    )


def main() -> None:
    client, bucket = get_s3_client()
    db = get_db_session()

    print("=== Checking completeness (DB → S3) ===")
    missing = check_completeness(client, bucket, db)
    print(f"\n{missing} missing file(s).")

    print("\n=== Checking for orphaned files (S3 → DB) ===")
    orphans = find_orphans(client, bucket, db)
    print_orphan_report(client, bucket, orphans)

    db.close()
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
