#!/usr/bin/env python3
"""Downsync: AWS S3 (Prod) → Dev MinIO.

Downloads files from the production AWS S3 bucket and uploads them
to the local MinIO instance, mapping the legacy prefix structure
to the new key structure. Removes local objects that no longer
exist in the production source (mirror mode).

Prefix mapping:
    AWS: standesdb-backup/{hash}  →  MinIO: standesdb/images/{hash}
    AWS: archive-backup/{hash}    →  MinIO: archive/store/{hash}

Usage:
    python scripts/downsync_from_prod_aws.py
    python scripts/downsync_from_prod_aws.py --dry-run
    python scripts/downsync_from_prod_aws.py --no-delete
    python scripts/downsync_from_prod_aws.py --verify-only
"""

import argparse
import os
import re
import sys
from pathlib import Path

import boto3
from botocore.client import BaseClient, Config
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import APP_ENVIRONMENT

SECRETS_PATH = "/run/secrets/aws-prod.env"

PREFIX_MAP = [
    ("standesdb-backup/", "standesdb/images/"),
    ("archive-backup/", "archive/store/"),
]

CACHE_PREFIXES = [
    ("standesdb/cache/", "standesdb/images/"),
    ("archive/cache/", "archive/store/"),
]

ARCHIVE_THUMB_RE = re.compile(r"^(.+)\.thumb_\w+$")


def load_env_file(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        with Path(path).open() as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return env


def get_aws_client(secrets: dict[str, str]) -> BaseClient:
    return boto3.client(
        "s3",
        aws_access_key_id=secrets.get("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=secrets.get("AWS_SECRET_ACCESS_KEY", ""),
        region_name=secrets.get("AWS_REGION", "eu-central-1"),
        config=Config(signature_version="s3v4"),
    )


def get_minio_client() -> BaseClient:
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000"),
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY", ""),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY", ""),
        region_name="us-east-1",
        config=Config(signature_version="s3v4"),
    )


def object_exists(client: BaseClient, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
    except ClientError:
        return False
    return True


def list_objects(client: BaseClient, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def map_key(aws_key: str) -> str | None:
    for aws_prefix, minio_prefix in PREFIX_MAP:
        if aws_key.startswith(aws_prefix):
            filename = aws_key[len(aws_prefix) :]
            if filename:
                return f"{minio_prefix}{filename}"
    return None


def derive_original_key(cache_key: str) -> str | None:
    """Derive the original file key from a cache/thumbnail key."""
    for cache_prefix, original_prefix in CACHE_PREFIXES:
        if not cache_key.startswith(cache_prefix):
            continue
        filename = cache_key[len(cache_prefix) :]
        match = ARCHIVE_THUMB_RE.match(filename)
        if match:
            return f"{original_prefix}{match.group(1)}"
        return f"{original_prefix}{filename}"
    return None


def _upload_object(
    aws: BaseClient,
    minio: BaseClient,
    aws_bucket: str,
    minio_bucket: str,
    aws_key: str,
    minio_key: str,
) -> bool:
    try:
        resp = aws.get_object(Bucket=aws_bucket, Key=aws_key)
        data = resp["Body"].read()
        ct = resp.get("ContentType", "application/octet-stream")
        minio.put_object(
            Bucket=minio_bucket,
            Key=minio_key,
            Body=data,
            ContentType=ct,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR: {aws_key} — {exc}")
        return False
    return True


def _delete_object(
    client: BaseClient,
    bucket: str,
    key: str,
    dry_run: bool,
    label: str = "",
) -> tuple[int, int]:
    if dry_run:
        print(f"  WOULD DELETE{label}: {key}")
        return 1, 0
    try:
        client.delete_object(Bucket=bucket, Key=key)
    except Exception as exc:  # noqa: BLE001
        print(f"  DELETE ERROR: {key} — {exc}")
        return 0, 1
    return 1, 0


def _sync_single_object(
    aws: BaseClient,
    minio: BaseClient,
    aws_bucket: str,
    minio_bucket: str,
    aws_key: str,
    minio_key: str,
    dry_run: bool,
) -> tuple[int, int, int]:
    if object_exists(minio, minio_bucket, minio_key):
        return 0, 1, 0

    if dry_run:
        print(f"  WOULD COPY: {aws_key} → {minio_key}")
        return 1, 0, 0

    if _upload_object(aws, minio, aws_bucket, minio_bucket, aws_key, minio_key):
        return 1, 0, 0
    return 0, 0, 1


def _sync_objects(
    aws: BaseClient,
    minio: BaseClient,
    aws_bucket: str,
    minio_bucket: str,
    aws_keys: list[str],
    dry_run: bool,
) -> tuple[int, int, int]:
    synced = 0
    skipped = 0
    errors = 0
    total = len(aws_keys)

    for i, aws_key in enumerate(aws_keys, 1):
        minio_key = map_key(aws_key)
        if not minio_key:
            continue

        s, sk, e = _sync_single_object(
            aws,
            minio,
            aws_bucket,
            minio_bucket,
            aws_key,
            minio_key,
            dry_run,
        )
        synced += s
        skipped += sk
        errors += e

        interval = 500 if sk else 50
        if i % interval == 0:
            print(f"  Progress: {i}/{total} (synced: {synced}, skipped: {skipped})")

    return synced, skipped, errors


def _delete_orphans(
    minio: BaseClient,
    minio_bucket: str,
    minio_prefix: str,
    expected_keys: set[str],
    dry_run: bool,
) -> tuple[int, int]:
    deleted = 0
    errors = 0

    local_keys = list_objects(minio, minio_bucket, minio_prefix)
    orphans = [k for k in local_keys if k not in expected_keys]

    if orphans:
        print(f"  Orphaned originals: {len(orphans)}")
    for key in orphans:
        d, e = _delete_object(minio, minio_bucket, key, dry_run)
        deleted += d
        errors += e

    return deleted, errors


def sync_prefix(
    aws: BaseClient,
    minio: BaseClient,
    aws_bucket: str,
    minio_bucket: str,
    aws_prefix: str,
    minio_prefix: str,
    dry_run: bool,
    no_delete: bool,
) -> tuple[int, int, int, int]:
    aws_keys = list_objects(aws, aws_bucket, aws_prefix)
    print(f"  Found {len(aws_keys)} objects in AWS under '{aws_prefix}'")

    expected_minio_keys: set[str] = {mk for k in aws_keys if (mk := map_key(k))}

    synced, skipped, errors = _sync_objects(
        aws,
        minio,
        aws_bucket,
        minio_bucket,
        aws_keys,
        dry_run,
    )

    if no_delete:
        return synced, skipped, errors, 0

    deleted, del_errors = _delete_orphans(
        minio,
        minio_bucket,
        minio_prefix,
        expected_minio_keys,
        dry_run,
    )
    return synced, skipped, errors + del_errors, deleted


def _is_orphaned_thumbnail(
    minio: BaseClient,
    minio_bucket: str,
    key: str,
) -> bool:
    original_key = derive_original_key(key)
    if not original_key:
        return False
    return not object_exists(minio, minio_bucket, original_key)


def cleanup_orphaned_thumbnails(
    minio: BaseClient,
    minio_bucket: str,
    dry_run: bool,
) -> tuple[int, int]:
    deleted = 0
    errors = 0

    for cache_prefix, _ in CACHE_PREFIXES:
        cache_keys = list_objects(minio, minio_bucket, cache_prefix)
        if not cache_keys:
            continue

        print(f"  Checking {len(cache_keys)} thumbnails in '{cache_prefix}'...")

        for key in cache_keys:
            if not _is_orphaned_thumbnail(minio, minio_bucket, key):
                continue
            d, e = _delete_object(
                minio,
                minio_bucket,
                key,
                dry_run,
                label=" orphaned thumb",
            )
            deleted += d
            errors += e

    return deleted, errors


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Downsync from AWS S3 (Prod) to Dev MinIO",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only count differences — do not sync or delete",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done — do not execute",
    )
    parser.add_argument(
        "--no-delete",
        action="store_true",
        help="Sync new files but do not delete local orphans",
    )
    return parser.parse_args()


def _verify_prefixes(
    aws: BaseClient,
    minio: BaseClient,
    aws_bucket: str,
    minio_bucket: str,
) -> None:
    for aws_prefix, minio_prefix in PREFIX_MAP:
        print(f"=== {aws_prefix} → {minio_prefix} ===")
        aws_keys = list_objects(aws, aws_bucket, aws_prefix)
        local_keys = set(list_objects(minio, minio_bucket, minio_prefix))
        missing = sum(
            1 for k in aws_keys if (mk := map_key(k)) and mk not in local_keys
        )
        orphans = sum(
            1
            for k in local_keys
            if k not in {map_key(ak) for ak in aws_keys if map_key(ak)}
        )
        print(
            f"  AWS: {len(aws_keys)}, Local: {len(local_keys)}, "
            f"Missing: {missing}, Orphans: {orphans}"
        )


def _run_sync(
    aws: BaseClient,
    minio: BaseClient,
    aws_bucket: str,
    minio_bucket: str,
    args: argparse.Namespace,
) -> None:
    total_synced = 0
    total_skipped = 0
    total_errors = 0
    total_deleted = 0

    for aws_prefix, minio_prefix in PREFIX_MAP:
        print(f"=== {aws_prefix} → {minio_prefix} ===")
        s, sk, e, d = sync_prefix(
            aws,
            minio,
            aws_bucket,
            minio_bucket,
            aws_prefix,
            minio_prefix,
            args.dry_run,
            args.no_delete,
        )
        total_synced += s
        total_skipped += sk
        total_errors += e
        total_deleted += d
        print(f"  Synced: {s}, Skipped: {sk}, Deleted: {d}, Errors: {e}")
        print()

    if not args.no_delete:
        print("=== Cleaning orphaned thumbnails ===")
        d, e = cleanup_orphaned_thumbnails(minio, minio_bucket, args.dry_run)
        total_deleted += d
        total_errors += e
        print(f"  Deleted: {d}, Errors: {e}")
        print()

    print(
        f"=== TOTAL: Synced: {total_synced}, "
        f"Skipped: {total_skipped}, "
        f"Deleted: {total_deleted}, "
        f"Errors: {total_errors} ==="
    )

    if total_errors:
        sys.exit(1)


def main() -> None:
    if APP_ENVIRONMENT == "production":
        print(
            "ERROR: This script must not run in production "
            f"(APP_ENVIRONMENT={APP_ENVIRONMENT!r})."
        )
        sys.exit(1)

    args = _parse_args()

    secrets = load_env_file(SECRETS_PATH)
    if not secrets.get("AWS_ACCESS_KEY_ID"):
        print(f"ERROR: No AWS credentials found in {SECRETS_PATH}")
        sys.exit(1)

    aws_bucket = secrets.get("AWS_BUCKET", "")
    if not aws_bucket:
        print(f"ERROR: AWS_BUCKET not set in {SECRETS_PATH}")
        sys.exit(1)

    minio_bucket = os.environ.get("S3_BUCKET", "vb-intern")
    aws = get_aws_client(secrets)
    minio = get_minio_client()

    print(f"Environment:  {APP_ENVIRONMENT}")
    print(f"AWS bucket:   {aws_bucket}")
    print(f"MinIO bucket: {minio_bucket}")
    print(f"Delete mode:  {'disabled' if args.no_delete else 'enabled'}")
    print()

    if args.verify_only:
        _verify_prefixes(aws, minio, aws_bucket, minio_bucket)
        return

    _run_sync(aws, minio, aws_bucket, minio_bucket, args)


if __name__ == "__main__":
    main()
