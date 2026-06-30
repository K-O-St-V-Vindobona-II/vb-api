#!/usr/bin/env python3
"""One-time migration: local filesystem → S3 (MinIO).

Uploads all files from /data/standesdb/ and /data/archive/ to the
configured S3 bucket, preserving the content-addressed key structure.

Usage:
    python scripts/migrate_to_s3.py [--verify-only] [--include-cache]
"""

import argparse
import os
import sys
from pathlib import Path

import boto3
from botocore.client import BaseClient, Config
from botocore.exceptions import ClientError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.archive_store_item import ArchiveStoreItem
from app.models.standesdb_image import StandesdbImage


def get_s3_client() -> tuple[BaseClient, str]:
    endpoint = os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000")
    access_key = os.environ.get("S3_ACCESS_KEY", "")
    secret_key = os.environ.get("S3_SECRET_KEY", "")
    bucket = os.environ.get("S3_BUCKET", "vb-intern")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(signature_version="s3v4"),
    )
    return client, bucket


def get_db_session() -> Session:
    db_url = os.environ.get(
        "DATABASE_URL",
        "sqlite:////database/legacy_db.sqlite3",
    )
    engine = create_engine(db_url)
    session_cls = sessionmaker(bind=engine)
    return session_cls()


def object_exists(client: BaseClient, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
    except ClientError:
        return False
    return True


def upload_directory(
    client: BaseClient,
    bucket: str,
    local_dir: Path,
    s3_prefix: str,
    content_types: dict[str, str],
) -> tuple[int, int, int]:
    uploaded = 0
    skipped = 0
    errors = 0

    if not local_dir.exists():
        print(f"  SKIP: {local_dir} does not exist")
        return uploaded, skipped, errors

    files = [f for f in local_dir.iterdir() if f.is_file()]
    total = len(files)
    print(f"  Found {total} files in {local_dir}")

    for i, file_path in enumerate(files, 1):
        key = f"{s3_prefix}/{file_path.name}"
        if object_exists(client, bucket, key):
            skipped += 1
            continue

        ct = content_types.get(
            file_path.name,
            "application/octet-stream",
        )
        try:
            data = file_path.read_bytes()
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=data,
                ContentType=ct,
            )
            uploaded += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: {key} — {exc}")
            errors += 1

        if i % 50 == 0:
            print(f"  Progress: {i}/{total}")

    return uploaded, skipped, errors


def build_content_type_map(db: Session) -> tuple[dict[str, str], dict[str, str]]:
    image_types: dict[str, str] = {}
    for img in db.query(StandesdbImage).all():
        if img.sha256_hash and img.type:
            image_types[img.sha256_hash] = img.type

    archive_types: dict[str, str] = {}
    for item in db.query(ArchiveStoreItem).all():
        if item.sha256_hash and item.mime_type:
            archive_types[item.sha256_hash] = item.mime_type

    return image_types, archive_types


def verify(client: BaseClient, bucket: str, db: Session) -> tuple[int, int]:
    found = 0
    missing = 0

    print("\nVerifying standesdb images...")
    for img in (
        db.query(StandesdbImage)
        .filter(
            StandesdbImage.deleted_at.is_(None),
        )
        .all()
    ):
        key = f"standesdb/images/{img.sha256_hash}"
        if object_exists(client, bucket, key):
            found += 1
        else:
            print(f"  MISSING: {key}")
            missing += 1

    print("Verifying archive store items...")
    for item in db.query(ArchiveStoreItem).all():
        key = f"archive/store/{item.sha256_hash}"
        if object_exists(client, bucket, key):
            found += 1
        else:
            print(f"  MISSING: {key}")
            missing += 1

    return found, missing


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate local files to S3",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify — do not upload",
    )
    parser.add_argument(
        "--include-cache",
        action="store_true",
        help="Also migrate cache/thumbnail files",
    )
    args = parser.parse_args()

    client, bucket = get_s3_client()
    db = get_db_session()

    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        print(f"Bucket '{bucket}' does not exist. Creating...")
        client.create_bucket(Bucket=bucket)

    if args.verify_only:
        found, missing = verify(client, bucket, db)
        print(f"\nVerification: {found} found, {missing} missing")
        sys.exit(1 if missing else 0)

    image_types, archive_types = build_content_type_map(db)
    total_uploaded = 0
    total_skipped = 0
    total_errors = 0

    standesdb_images = Path(
        os.environ.get(
            "STANDESDB_IMAGES_PATH",
            "/data/standesdb/images",
        )
    )
    archive_store = Path(
        os.environ.get(
            "ARCHIVE_STORE_PATH",
            "/data/archive/store",
        )
    )

    print("=== Migrating standesdb images ===")
    u, s, e = upload_directory(
        client,
        bucket,
        standesdb_images,
        "standesdb/images",
        image_types,
    )
    total_uploaded += u
    total_skipped += s
    total_errors += e
    print(f"  Uploaded: {u}, Skipped: {s}, Errors: {e}")

    print("\n=== Migrating archive store ===")
    u, s, e = upload_directory(
        client,
        bucket,
        archive_store,
        "archive/store",
        archive_types,
    )
    total_uploaded += u
    total_skipped += s
    total_errors += e
    print(f"  Uploaded: {u}, Skipped: {s}, Errors: {e}")

    if args.include_cache:
        standesdb_cache = Path(
            os.environ.get(
                "STANDESDB_CACHE_PATH",
                "/data/standesdb/cache",
            )
        )
        archive_cache = Path(
            os.environ.get(
                "ARCHIVE_CACHE_PATH",
                "/data/archive/cache",
            )
        )

        print("\n=== Migrating standesdb cache ===")
        u, s, e = upload_directory(
            client,
            bucket,
            standesdb_cache,
            "standesdb/cache",
            {},
        )
        total_uploaded += u
        total_skipped += s
        total_errors += e
        print(f"  Uploaded: {u}, Skipped: {s}, Errors: {e}")

        print("\n=== Migrating archive cache ===")
        u, s, e = upload_directory(
            client,
            bucket,
            archive_cache,
            "archive/cache",
            {},
        )
        total_uploaded += u
        total_skipped += s
        total_errors += e
        print(f"  Uploaded: {u}, Skipped: {s}, Errors: {e}")

    print(
        f"\n=== TOTAL: Uploaded: {total_uploaded}, "
        f"Skipped: {total_skipped}, Errors: {total_errors} ==="
    )

    print("\n=== Running verification ===")
    found, missing = verify(client, bucket, db)
    print(f"\nVerification: {found} found, {missing} missing")

    db.close()

    if total_errors or missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
