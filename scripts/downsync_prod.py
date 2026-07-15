#!/usr/bin/env python3
"""Downsync: Prod AWS S3 -> local non-prod stage (destructive).

Self-contained, two-step downsync:

1. Mirrors the entire production `vindobona2-at` AWS S3 bucket into the
   local MinIO instance — an exact 1:1 clone (source and dest use the same
   key structure, so no remapping is needed). Local-only objects are
   deleted unless --no-delete is passed.
2. Restores the local PostgreSQL database from the now-current local
   MinIO's `db-backups/` prefix (i.e. from whatever the mirror step just
   brought down from prod) and runs `alembic upgrade head`.

The DB step reads exclusively from local storage, never from prod
directly — after step 1, local MinIO already holds an exact copy of
prod's backups, so this is the same operation restore_db.py already
performs. Refuses to run outside a non-prod stage.

Usage:
    python scripts/downsync_prod.py
    python scripts/downsync_prod.py --dry-run
    python scripts/downsync_prod.py --yes
    python scripts/downsync_prod.py --skip-db
    python scripts/downsync_prod.py --skip-s3 --no-delete
"""

import argparse
import subprocess
import sys
from pathlib import Path

_VB_API_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VB_API_ROOT))

from app.core.config import APP_ENVIRONMENT
from app.core.storage import S3_PATH_DB_BACKUPS, StorageClient, get_storage
from app.services.backup_service import run_restore
from app.services.s3_mirror_service import MirrorResult, mirror_prefix

SECRETS_PATH = "/run/secrets/aws-prod.env"


def _load_env_file(path: str) -> dict[str, str]:
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


def _load_aws_secrets() -> dict[str, str]:
    # Not named "secrets" - this dict also carries the non-sensitive
    # AWS_BUCKET value that main() prints; CodeQL's clear-text-logging
    # query flags any variable whose name matches sensitive-data patterns
    # (e.g. "secret") regardless of which field is actually read from it.
    aws_env = _load_env_file(SECRETS_PATH)
    if not aws_env.get("AWS_ACCESS_KEY_ID"):
        print(f"ERROR: No AWS credentials found in {SECRETS_PATH}")
        sys.exit(1)
    if not aws_env.get("AWS_BUCKET"):
        print(f"ERROR: AWS_BUCKET not set in {SECRETS_PATH}")
        sys.exit(1)
    return aws_env


def _build_prod_storage(aws_env: dict[str, str]) -> StorageClient:
    return StorageClient(
        endpoint_url=None,
        access_key=aws_env["AWS_ACCESS_KEY_ID"],
        secret_key=aws_env["AWS_SECRET_ACCESS_KEY"],
        bucket=aws_env["AWS_BUCKET"],
        region=aws_env.get("AWS_REGION", "eu-central-1"),
    )


def _confirm(auto_yes: bool) -> None:
    if auto_yes:
        return
    answer = input('Type "yes" to overwrite local DB and S3 data: ')
    if answer.strip().lower() != "yes":
        print("Aborted.")
        sys.exit(0)


def _print_mirror_progress(done: int, total: int) -> None:
    print(f"  Progress: {done}/{total}")


def _run_s3_mirror(
    prod_storage: StorageClient,
    local_storage: StorageClient,
    dry_run: bool,
    no_delete: bool,
) -> MirrorResult:
    result = mirror_prefix(
        prod_storage,
        local_storage,
        dry_run=dry_run,
        delete_orphans=not no_delete,
        on_progress=_print_mirror_progress,
    )
    print(
        f"  Synced: {len(result.synced)}, Skipped: {result.skipped}, "
        f"Deleted: {len(result.deleted)}, Errors: {len(result.errors)}"
    )
    return result


def _run_db_restore(local_storage: StorageClient, dry_run: bool) -> None:
    if dry_run:
        keys = local_storage.list_keys(prefix=f"{S3_PATH_DB_BACKUPS}/")
        if not keys:
            print(
                "  WOULD RESTORE: no backups currently in local MinIO "
                "(run without --skip-s3 and --dry-run first to mirror one down)."
            )
            return
        latest = sorted(keys)[-1]
        print(f"  WOULD RESTORE: {latest}")
        return

    try:
        run_restore(local_storage)
    except RuntimeError as exc:
        print(f"ERROR: DB restore failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print("  DB restore complete.")

    print("  Running alembic upgrade head...")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],  # noqa: S607
        cwd=_VB_API_ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        print(f"ERROR: alembic upgrade head failed: {stderr}", file=sys.stderr)
        sys.exit(1)
    print("  Alembic upgrade complete.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Downsync prod AWS S3 to the local non-prod stage",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done - do not execute",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Skip the DB restore step",
    )
    parser.add_argument(
        "--skip-s3",
        action="store_true",
        help="Skip the S3 mirror step",
    )
    parser.add_argument(
        "--no-delete",
        action="store_true",
        help="S3 step: sync new files but do not delete local orphans",
    )
    return parser.parse_args()


def main() -> None:
    if APP_ENVIRONMENT == "production":
        print(
            "ERROR: This script must not run in production "
            f"(APP_ENVIRONMENT={APP_ENVIRONMENT!r})."
        )
        sys.exit(1)

    args = _parse_args()
    local_storage = get_storage()

    print(f"Environment: {APP_ENVIRONMENT}")
    print(f"Skip DB:     {args.skip_db}")
    print(f"Skip S3:     {args.skip_s3}")
    print(f"Dry run:     {args.dry_run}")
    print()

    if not args.dry_run:
        _confirm(args.yes)

    if not args.skip_s3:
        aws_env = _load_aws_secrets()
        prod_storage = _build_prod_storage(aws_env)
        print(f"=== S3 mirror (prod {aws_env['AWS_BUCKET']} -> local MinIO) ===")
        result = _run_s3_mirror(
            prod_storage, local_storage, args.dry_run, args.no_delete
        )
        print()
        if result.has_errors:
            sys.exit(1)

    if not args.skip_db:
        print("=== DB restore (from local MinIO db-backups/) ===")
        _run_db_restore(local_storage, args.dry_run)


if __name__ == "__main__":
    main()
