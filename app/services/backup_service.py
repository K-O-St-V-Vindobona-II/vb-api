import logging
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import APP_ENVIRONMENT
from app.core.storage import S3_PATH_DB_BACKUPS, StorageClient

logger = logging.getLogger(__name__)

BACKUP_RETENTION_DAYS: int = int(os.environ.get("BACKUP_RETENTION_DAYS", "29"))

_TIMESTAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"
_TIMESTAMP_LENGTH = len("2026-07-15_03-00-00")  # fixed-width strftime() output


def _require_postgres(database_url: str) -> None:
    """Raise RuntimeError if DATABASE_URL is not a PostgreSQL URL."""
    if not database_url.startswith("postgresql"):
        msg = f"Backup requires PostgreSQL. Got: {database_url[:30]}..."
        raise RuntimeError(msg)


def _resolve_pg_tool(name: str) -> str:
    """Return the absolute path of a pg_dump/pg_restore tool or raise."""
    path = shutil.which(name)
    if path is None:
        msg = f"'{name}' not found in PATH. Install postgresql-client."
        raise RuntimeError(msg)
    return path


def _parse_db_url(database_url: str) -> tuple[str, str, str, int, str]:
    """Return (host, username, password, port, dbname).

    Uses SQLAlchemy's own connection-string parser (the same one
    `create_engine(DATABASE_URL)` uses elsewhere in this app) rather than
    `urllib.parse.urlparse` — the latter is built for generic HTTP-style
    URLs and can misparse passwords containing URL-reserved characters
    (e.g. raised `ValueError: Port could not be cast to integer value`
    against a real production password that `create_engine()` handles
    without issue).
    """
    url = make_url(database_url)
    return (
        url.host or "localhost",
        url.username or "",
        url.password or "",
        url.port or 5432,
        url.database or "",
    )


def _build_pg_env(password: str) -> dict[str, str]:
    """Build subprocess env with PGPASSWORD injected."""
    return {**os.environ, "PGPASSWORD": password}


def _run_pg_subprocess(
    args: list[str], env: dict[str, str], tool_name: str
) -> subprocess.CompletedProcess[bytes]:
    """Run a pg_dump/pg_restore subprocess.

    On failure, raises RuntimeError with the tool's actual stderr — a bare
    `subprocess.CalledProcessError` hides stderr from the caller, which
    makes a disaster-recovery script undebuggable exactly when it matters
    most (e.g. it previously surfaced only "returned non-zero exit status
    1" with no indication of what pg_restore actually complained about).
    """
    try:
        return subprocess.run(args, capture_output=True, env=env, check=True)  # noqa: S603
    except subprocess.CalledProcessError as exc:
        stderr = (
            exc.stderr.decode("utf-8", errors="replace").strip()
            if exc.stderr
            else "(no stderr captured)"
        )
        msg = f"{tool_name} failed (exit {exc.returncode}): {stderr}"
        raise RuntimeError(msg) from exc


def run_backup(storage: StorageClient, *, manual: bool = False) -> str:
    """
    Dump the PostgreSQL database and upload to S3.

    manual=True tags the filename with a "-manual" suffix, distinguishing
    ad-hoc backups (CLI script, admin-triggered API call) from the ones
    the scheduled db_backup job produces unsuffixed.

    Returns the backup filename (without path prefix).
    Raises RuntimeError on pg_dump failure or S3 upload error.
    """
    database_url = os.environ.get("DATABASE_URL", "")
    _require_postgres(database_url)

    host, user, password, port, dbname = _parse_db_url(database_url)

    timestamp = datetime.now(UTC).strftime(_TIMESTAMP_FORMAT)
    suffix = "-manual" if manual else ""
    backup_name = f"{APP_ENVIRONMENT}-{timestamp}{suffix}.dump"
    s3_key = f"{S3_PATH_DB_BACKUPS}/{backup_name}"

    logger.info("Starting DB backup: %s", backup_name)

    pg_dump = _resolve_pg_tool("pg_dump")
    result = _run_pg_subprocess(
        [
            pg_dump,
            "--format=custom",
            f"--host={host}",
            f"--port={port}",
            f"--username={user}",
            f"--dbname={dbname}",
        ],
        env=_build_pg_env(password),
        tool_name="pg_dump",
    )

    storage.upload(key=s3_key, data=result.stdout)
    logger.info("DB backup complete: %s (%d bytes)", backup_name, len(result.stdout))
    return backup_name


def run_restore(
    storage: StorageClient,
    backup_name: str | None = None,
    *,
    force: bool = False,
) -> None:
    """
    Restore the PostgreSQL database from an S3 backup.

    If backup_name is None, restores the latest available backup.
    Requires force=True when APP_ENVIRONMENT == 'production'.
    """
    database_url = os.environ.get("DATABASE_URL", "")
    _require_postgres(database_url)

    if APP_ENVIRONMENT == "production" and not force:
        msg = (
            "Restore in production requires explicit force=True. "
            "This operation is destructive."
        )
        raise RuntimeError(msg)

    if backup_name is None:
        keys = storage.list_keys(prefix=f"{S3_PATH_DB_BACKUPS}/")
        if not keys:
            msg = "No backups found in S3."
            raise RuntimeError(msg)
        backup_name = sorted(keys)[-1].removeprefix(f"{S3_PATH_DB_BACKUPS}/")
        logger.info("Auto-selected latest backup: %s", backup_name)

    s3_key = f"{S3_PATH_DB_BACKUPS}/{backup_name}"
    logger.info("Downloading backup from S3: %s", s3_key)
    data = storage.download(key=s3_key)

    host, user, password, port, dbname = _parse_db_url(database_url)

    with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    logger.info("Restoring DB from backup: %s", backup_name)
    pg_restore = _resolve_pg_tool("pg_restore")
    try:
        _run_pg_subprocess(
            [
                pg_restore,
                "--clean",
                "--if-exists",
                f"--host={host}",
                f"--port={port}",
                f"--username={user}",
                f"--dbname={dbname}",
                tmp_path,
            ],
            env=_build_pg_env(password),
            tool_name="pg_restore",
        )
    finally:
        Path(tmp_path).unlink()

    logger.info("DB restore complete from: %s", backup_name)


def _parse_backup_timestamp(backup_name: str) -> datetime | None:
    """Extract the UTC timestamp from a
    '[env]-YYYY-MM-DD_HH-MM-SS[-manual].dump' name (the optional "-manual"
    suffix, and any other trailing text, is ignored)."""
    if not backup_name.endswith(".dump"):
        return None
    stem = backup_name.removesuffix(".dump")
    try:
        _, ts_part = stem.split("-", 1)
        return datetime.strptime(
            ts_part[:_TIMESTAMP_LENGTH], _TIMESTAMP_FORMAT
        ).replace(tzinfo=UTC)
    except ValueError:
        return None


def cleanup_old_backups(
    storage: StorageClient,
    retention_days: int = BACKUP_RETENTION_DAYS,
) -> list[str]:
    """
    Delete S3 backups older than retention_days.

    Returns the list of deleted backup names. Names that cannot be parsed
    as a valid backup timestamp are skipped (never deleted blindly).
    """
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    deleted: list[str] = []

    for key in storage.list_keys(prefix=f"{S3_PATH_DB_BACKUPS}/"):
        name = key.removeprefix(f"{S3_PATH_DB_BACKUPS}/")
        backup_date = _parse_backup_timestamp(name)
        if backup_date is None or backup_date >= cutoff:
            continue
        storage.delete(key)
        deleted.append(name)
        logger.info("Deleted expired backup (older than %dd): %s", retention_days, name)

    return deleted
