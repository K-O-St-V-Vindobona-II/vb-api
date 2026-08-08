import logging
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.storage import S3_PATH_DB_BACKUPS, StorageClient

logger = logging.getLogger(__name__)

BACKUP_RETENTION_DAYS: int = get_settings().backup_retention_days

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
    database_url = cast("str", get_settings().database_url)
    _require_postgres(database_url)

    host, user, password, port, dbname = _parse_db_url(database_url)

    timestamp = datetime.now(UTC).strftime(_TIMESTAMP_FORMAT)
    suffix = "-manual" if manual else ""
    backup_name = f"{get_settings().app_environment}-{timestamp}{suffix}.dump"
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
            # scheduled_task_runs is stage-local by design (each stage's
            # scheduler is configured independently) — keep the table
            # structure in the dump (so restore/downsync never leaves it
            # missing) but never carry another stage's run history along.
            "--exclude-table-data=public.scheduled_task_runs",
        ],
        env=_build_pg_env(password),
        tool_name="pg_dump",
    )

    storage.upload(key=s3_key, data=result.stdout)
    logger.info("DB backup complete: %s (%d bytes)", backup_name, len(result.stdout))
    return backup_name


def _wipe_public_schema(
    host: str, user: str, password: str, port: int, dbname: str
) -> None:
    """Drop and recreate the 'public' schema before a full restore.

    pg_restore --clean computes DROP order from the dump's dependency
    graph, which can get self-referencing foreign keys wrong (e.g. a
    table with a FK to itself, plus another table's FK into the same
    primary key) - it then aborts that one DROP with "cannot drop
    constraint ... because other objects depend on it", and pg_restore's
    default error-tolerant behavior silently continues past the failure,
    leaving the target schema in an inconsistent, partially-restored
    state (observed in practice: a 44 MB production dump this actually
    happened with). Wiping the schema upfront and restoring without
    --clean sidesteps the ordering problem entirely - there is nothing
    left to drop, so no DROP order can ever be wrong.

    This runs against the *live* app database (this is what makes
    restore/downsync work in the first place) - DROP SCHEMA needs an
    ACCESS EXCLUSIVE lock, which any other session with an open
    transaction on this database can block. Racing that with a
    lock_timeout alone is unreliable in practice: this app's own
    background session-refresh timer (see useSessionManager.ts) keeps a
    steady trickle of ordinary requests arriving, so a fixed wait can
    lose to ambient traffic even when nothing is actually wrong (observed
    in practice after adding a first lock_timeout=5s fix). Terminating
    every other session on this database first makes lock acquisition
    deterministic instead of a timing race - any session still using the
    *old* schema is about to get errors the instant it's dropped anyway,
    so disconnecting it cleanly now is strictly better than letting it
    fail later with a confusing "relation does not exist". The
    scheduler's own advisory-lock-holding connection (see
    _acquire_scheduler_lock() in scheduler.py) is deliberately spared -
    it never touches a table, so it can never conflict with DROP SCHEMA,
    and terminating it would silently stop this worker's scheduled jobs
    until the next restart. lock_timeout stays as a safety net for the
    unlikely case of a new connection arriving in the brief window
    between the terminate and the DROP SCHEMA - a fast, clearly logged
    failure instead of the unbounded hang from before this function had
    any timeout at all (observed in practice: an idle-in-transaction
    session froze an entire dev stage, including login, for 20+ minutes
    with no self-recovery).
    """
    psql = _resolve_pg_tool("psql")
    _run_pg_subprocess(
        [
            psql,
            f"--host={host}",
            f"--port={port}",
            f"--username={user}",
            f"--dbname={dbname}",
            "-c",
            (
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND pid != pg_backend_pid() "
                "AND query NOT ILIKE '%pg_try_advisory_lock%'; "
                "SET lock_timeout = '5s'; DROP SCHEMA public CASCADE; "
                "CREATE SCHEMA public;"
            ),
        ],
        env=_build_pg_env(password),
        tool_name="psql",
    )


def _snapshot_local_job_history(
    host: str, user: str, password: str, port: int, dbname: str
) -> bytes:
    """Dump this stage's own scheduled_task_runs rows before a restore
    wipes them.

    scheduled_task_runs is stage-local by design (see run_backup()'s
    --exclude-table-data) - a restore/downsync must not let it come back
    empty just because the schema got wiped first. Data-only, single-table
    dump so this stays a cheap, non-blocking MVCC snapshot of the live
    table, taken before _wipe_public_schema() terminates any other
    session.
    """
    pg_dump = _resolve_pg_tool("pg_dump")
    result = _run_pg_subprocess(
        [
            pg_dump,
            "--format=custom",
            "--data-only",
            "--table=public.scheduled_task_runs",
            f"--host={host}",
            f"--port={port}",
            f"--username={user}",
            f"--dbname={dbname}",
        ],
        env=_build_pg_env(password),
        tool_name="pg_dump",
    )
    return result.stdout


def _restore_local_job_history(
    snapshot: bytes, host: str, user: str, password: str, port: int, dbname: str
) -> None:
    """Re-insert this stage's own pre-restore scheduled_task_runs rows.

    Runs after the main pg_restore, once the (now-empty, since the
    restored dump excludes this table's data) table structure exists
    again. --data-only against an empty snapshot is a harmless no-op, so
    this needs no special-casing for a fresh stage with no prior history.
    """
    pg_restore = _resolve_pg_tool("pg_restore")
    with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tmp:
        tmp.write(snapshot)
        tmp_path = tmp.name
    try:
        _run_pg_subprocess(
            [
                pg_restore,
                "--data-only",
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


def run_restore(
    storage: StorageClient,
    backup_name: str | None = None,
    *,
    force: bool = False,
) -> str:
    """
    Restore the PostgreSQL database from an S3 backup.

    If backup_name is None, restores the latest available backup.
    Requires force=True when APP_ENVIRONMENT == 'production'.
    Returns the name of the backup that was actually restored.

    This stage's own scheduled_task_runs history survives the restore
    (snapshotted before the wipe, re-inserted after) - the restored dump
    itself never carries scheduled_task_runs data across stages (see
    run_backup()'s --exclude-table-data), only this stage's pre-restore
    rows are ever re-applied here.
    """
    database_url = cast("str", get_settings().database_url)
    _require_postgres(database_url)

    if get_settings().app_environment == "production" and not force:
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
        local_job_history = _snapshot_local_job_history(
            host, user, password, port, dbname
        )
        _wipe_public_schema(host, user, password, port, dbname)
        _run_pg_subprocess(
            [
                pg_restore,
                f"--host={host}",
                f"--port={port}",
                f"--username={user}",
                f"--dbname={dbname}",
                tmp_path,
            ],
            env=_build_pg_env(password),
            tool_name="pg_restore",
        )
        _restore_local_job_history(
            local_job_history, host, user, password, port, dbname
        )
    finally:
        Path(tmp_path).unlink()

    logger.info("DB restore complete from: %s", backup_name)
    return backup_name


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
