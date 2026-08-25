"""Tests for backup_service — run_backup, run_restore, cleanup_old_backups."""

import os
from datetime import datetime
from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import boto3
import pytest
from botocore.exceptions import ClientError

from app.core.storage import S3_PATH_DB_BACKUPS, StorageClient
from app.services.backup_service import (
    _parse_backup_timestamp,
    _parse_db_url,
    _retry_transient_s3,
    cleanup_old_backups,
    run_backup,
    run_restore,
)

BACKUP_BUCKET = "backup-tests"
PG_URL = "postgresql://user:secret@localhost:5432/testdb"
SQLITE_URL = "sqlite:///test.db"
FAKE_PG_DUMP = "/usr/bin/pg_dump"
FAKE_PG_RESTORE = "/usr/bin/pg_restore"
FAKE_PSQL = "/usr/bin/psql"
PATCH_WHICH = "app.services.backup_service.shutil.which"

_FAKE_PG_TOOLS = {
    "pg_dump": FAKE_PG_DUMP,
    "pg_restore": FAKE_PG_RESTORE,
    "psql": FAKE_PSQL,
}


def _which_side_effect(name: str) -> str:
    return _FAKE_PG_TOOLS[name]


_S3_CREDS = {
    "region_name": "us-east-1",
    "aws_access_key_id": "testing",
    "aws_secret_access_key": "testing",
}


@pytest.fixture(scope="session")
def backup_bucket(_moto_env):
    """Create a dedicated S3 bucket for backup tests within the session mock."""
    boto3.client("s3", **_S3_CREDS).create_bucket(Bucket=BACKUP_BUCKET)
    return BACKUP_BUCKET


@pytest.fixture(autouse=True)
def _clean_backup_prefix(backup_bucket):
    """Wipe the backup prefix before each test for isolation."""
    client = boto3.client("s3", **_S3_CREDS)
    resp = client.list_objects_v2(Bucket=backup_bucket, Prefix=f"{S3_PATH_DB_BACKUPS}/")
    for obj in resp.get("Contents", []):
        client.delete_object(Bucket=backup_bucket, Key=obj["Key"])
    return


def _make_storage() -> StorageClient:
    return StorageClient(
        endpoint_url="https://s3.amazonaws.com",
        access_key="testing",
        secret_key="testing",
        bucket=BACKUP_BUCKET,
    )


def _put_backup(storage: StorageClient, name: str) -> None:
    storage.upload(key=f"{S3_PATH_DB_BACKUPS}/{name}", data=b"fake-dump")


class TestParseBackupTimestamp:
    def test_valid_development(self):
        dt = _parse_backup_timestamp("development-2026-06-30_03-00-00.dump")
        assert dt == datetime(2026, 6, 30, 3, 0, 0, tzinfo=ZoneInfo("Europe/Vienna"))

    def test_valid_production(self):
        dt = _parse_backup_timestamp("production-2025-01-15_22-30-45.dump")
        assert dt == datetime(2025, 1, 15, 22, 30, 45, tzinfo=ZoneInfo("Europe/Vienna"))

    def test_valid_qa(self):
        assert _parse_backup_timestamp("qa-2024-12-01_00-00-00.dump") is not None

    def test_valid_test(self):
        assert _parse_backup_timestamp("test-2024-11-01_12-00-00.dump") is not None

    def test_invalid_no_extension(self):
        assert _parse_backup_timestamp("development-2026-06-30_03-00-00") is None

    def test_invalid_garbage(self):
        assert _parse_backup_timestamp("not-a-backup.dump") is None

    def test_invalid_empty(self):
        assert _parse_backup_timestamp("") is None

    def test_valid_manual_suffix(self):
        dt = _parse_backup_timestamp("development-2026-06-30_03-00-00-manual.dump")
        assert dt == datetime(2026, 6, 30, 3, 0, 0, tzinfo=ZoneInfo("Europe/Vienna"))


class TestParseDbUrl:
    def test_simple_url(self):
        host, user, password, port, dbname = _parse_db_url(
            "postgresql://user:secret@localhost:5432/testdb"
        )
        assert (host, user, password, port, dbname) == (
            "localhost",
            "user",
            "secret",
            5432,
            "testdb",
        )

    def test_default_port_when_missing(self):
        _, _, _, port, _ = _parse_db_url("postgresql://user:secret@localhost/testdb")
        assert port == 5432

    def test_password_containing_slash(self):
        """Regression test: urllib.parse.urlparse treats the first '/'
        after '://' as the start of the path, so a password containing
        '/' (common in randomly-generated passwords, e.g. base64-derived)
        makes it misparse the whole netloc and raise `ValueError: Port
        could not be cast to integer value` — exactly what happened
        against a real production password. make_url() (the same parser
        create_engine() already uses successfully for this DATABASE_URL
        elsewhere in the app) handles it correctly."""
        host, user, password, port, dbname = _parse_db_url(
            "postgresql://vb:has/slash@localhost:5432/vb"
        )
        assert (host, user, password, port, dbname) == (
            "localhost",
            "vb",
            "has/slash",
            5432,
            "vb",
        )

    def test_password_containing_other_reserved_characters(self):
        host, _user, password, port, dbname = _parse_db_url(
            "postgresql://vb:has#hash?and=query@localhost:5432/vb"
        )
        assert password == "has#hash?and=query"
        assert host == "localhost"
        assert port == 5432
        assert dbname == "vb"


class TestRunBackup:
    def test_run_backup_success(self, backup_bucket):
        storage = _make_storage()
        fake_dump = b"PG_DUMP_DATA"
        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, return_value=FAKE_PG_DUMP),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(stdout=fake_dump, returncode=0)
            name = run_backup(storage)

        assert name.endswith(".dump")
        data = storage.download(f"{S3_PATH_DB_BACKUPS}/{name}")
        assert data == fake_dump

    def test_run_backup_key_format(self, backup_bucket):
        storage = _make_storage()
        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, return_value=FAKE_PG_DUMP),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(stdout=b"x", returncode=0)
            name = run_backup(storage)

        env_part = name.split("-")[0]
        assert env_part == os.environ.get("APP_ENVIRONMENT", "test")
        assert _parse_backup_timestamp(name) is not None

    def test_run_backup_excludes_scheduled_task_runs_data(self, backup_bucket):
        """scheduled_task_runs is stage-local by design (see its model
        docstring) — must never be carried across stages via backup/
        restore/downsync, but the table structure itself must survive so a
        restore never leaves it permanently missing."""
        storage = _make_storage()
        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, return_value=FAKE_PG_DUMP),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(stdout=b"x", returncode=0)
            run_backup(storage)

        args = mock_run.call_args.args[0]
        assert "--exclude-table-data=public.scheduled_task_runs" in args
        assert not any(a.startswith("--exclude-table=") for a in args)

    def test_run_backup_not_postgres(self):
        storage = MagicMock(spec=StorageClient)
        with (
            patch.dict(os.environ, {"DATABASE_URL": SQLITE_URL}),
            pytest.raises(RuntimeError, match="Backup requires PostgreSQL"),
        ):
            run_backup(storage)

    def test_run_backup_pg_dump_fails(self, backup_bucket):
        storage = _make_storage()
        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, return_value=FAKE_PG_DUMP),
            patch(
                "subprocess.run",
                side_effect=CalledProcessError(
                    1, "pg_dump", stderr=b"FATAL: password authentication failed"
                ),
            ),
            pytest.raises(RuntimeError, match="password authentication failed"),
        ):
            run_backup(storage)

    def test_run_backup_pg_dump_not_installed(self):
        storage = MagicMock(spec=StorageClient)
        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, return_value=None),
            pytest.raises(RuntimeError, match="pg_dump"),
        ):
            run_backup(storage)

    def test_run_backup_manual_adds_suffix(self, backup_bucket):
        storage = _make_storage()
        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, return_value=FAKE_PG_DUMP),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(stdout=b"x", returncode=0)
            name = run_backup(storage, manual=True)

        assert name.endswith("-manual.dump")
        assert _parse_backup_timestamp(name) is not None

    def test_run_backup_scheduled_has_no_suffix(self, backup_bucket):
        storage = _make_storage()
        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, return_value=FAKE_PG_DUMP),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(stdout=b"x", returncode=0)
            name = run_backup(storage)

        assert "-manual" not in name

    def test_run_backup_uses_app_timezone_not_utc(self, backup_bucket):
        """Regression guard for the 2026-08-25 production incident: the
        embedded timestamp used to be datetime.now(UTC), which silently
        diverged from the Vienna wall-clock time an admin actually sees
        (the container has TZ=Europe/Vienna set) - exactly the
        DST-dependent 1-2h skew local_now() exists to prevent everywhere
        else in the app."""
        storage = _make_storage()
        fixed_now = datetime(2026, 8, 25, 13, 25, 11, tzinfo=ZoneInfo("Europe/Vienna"))
        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, return_value=FAKE_PG_DUMP),
            patch("subprocess.run") as mock_run,
            patch("app.services.backup_service.local_now", return_value=fixed_now),
        ):
            mock_run.return_value = MagicMock(stdout=b"x", returncode=0)
            name = run_backup(storage)

        assert "2026-08-25_13-25-11" in name


class TestRunRestore:
    def test_run_restore_specific_name(self, backup_bucket):
        storage = _make_storage()
        backup_name = "test-2026-01-01_03-00-00.dump"
        _put_backup(storage, backup_name)

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, side_effect=_which_side_effect),
            patch("subprocess.run") as mock_run,
            patch.object(Path, "unlink"),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=b"t"),  # table-exists check
                MagicMock(returncode=0, stdout=b"snapshot-bytes"),  # snapshot dump
                MagicMock(returncode=0),  # wipe
                MagicMock(returncode=0),  # main restore
                MagicMock(returncode=0),  # ANALYZE (restore verification)
                MagicMock(returncode=0, stdout=b"5"),  # row count (verification)
                MagicMock(returncode=0),  # local history restore
            ]
            restored = run_restore(storage, backup_name=backup_name)

        assert restored == backup_name
        assert mock_run.call_count == 7
        restore_args = mock_run.call_args_list[3][0][0]
        assert "pg_restore" in restore_args[0]

    def test_run_restore_snapshots_and_restores_local_job_history(self, backup_bucket):
        """scheduled_task_runs is stage-local (see run_backup()'s
        --exclude-table-data) - a restore must not silently discard this
        stage's own job-run history just because the schema gets wiped.
        Regression guard for the 2026-08-09 finding: the table used to come
        back empty after every restore/downsync."""
        storage = _make_storage()
        backup_name = "test-2026-01-01_03-00-00.dump"
        _put_backup(storage, backup_name)
        local_snapshot = b"FAKE_LOCAL_JOB_HISTORY_DUMP"

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, side_effect=_which_side_effect),
            patch("subprocess.run") as mock_run,
            patch.object(Path, "unlink"),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=b"t"),  # table-exists check
                MagicMock(returncode=0, stdout=local_snapshot),  # snapshot dump
                MagicMock(returncode=0),  # wipe
                MagicMock(returncode=0),  # main restore
                MagicMock(returncode=0),  # ANALYZE (restore verification)
                MagicMock(returncode=0, stdout=b"5"),  # row count (verification)
                MagicMock(returncode=0),  # local history restore
            ]
            run_restore(storage, backup_name=backup_name)

        assert mock_run.call_count == 7
        snapshot_args = mock_run.call_args_list[1][0][0]
        wipe_args = mock_run.call_args_list[2][0][0]
        main_restore_args = mock_run.call_args_list[3][0][0]
        history_restore_args = mock_run.call_args_list[6][0][0]

        # Snapshot must run BEFORE the wipe, against the still-live table.
        assert snapshot_args[0] == FAKE_PG_DUMP
        assert "--data-only" in snapshot_args
        assert "--table=public.scheduled_task_runs" in snapshot_args
        assert wipe_args[0] == FAKE_PSQL

        # Local history is re-applied AFTER the main restore, data-only
        # (the table structure already exists from the main restore).
        assert main_restore_args[0] == FAKE_PG_RESTORE
        assert "--data-only" not in main_restore_args
        assert history_restore_args[0] == FAKE_PG_RESTORE
        assert "--data-only" in history_restore_args

        # The snapshot bytes actually flow into the local-restore temp file.
        history_tmp_path = history_restore_args[-1]
        assert Path(history_tmp_path).read_bytes() == local_snapshot

    def test_run_restore_skips_snapshot_when_table_does_not_exist(self, backup_bucket):
        """Regression guard: restoring into a brand-new, still schema-less
        database (e.g. right after a Postgres major-version upgrade, or the
        first restore onto a freshly (re-)installed host) used to abort
        immediately - pg_dump raised "no matching tables were found" for
        scheduled_task_runs before the restore ever got a chance to create
        it. The snapshot/re-insert pair must be skipped entirely in that
        case, not attempted and swallowed."""
        storage = _make_storage()
        backup_name = "test-2026-01-01_03-00-00.dump"
        _put_backup(storage, backup_name)

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, side_effect=_which_side_effect),
            patch("subprocess.run") as mock_run,
            patch.object(Path, "unlink"),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=b"f"),  # table-exists check: no
                MagicMock(returncode=0),  # wipe
                MagicMock(returncode=0),  # main restore
                MagicMock(returncode=0),  # ANALYZE (restore verification)
                MagicMock(returncode=0, stdout=b"5"),  # row count (verification)
            ]
            run_restore(storage, backup_name=backup_name)

        assert mock_run.call_count == 5
        table_exists_args = mock_run.call_args_list[0][0][0]
        wipe_args = mock_run.call_args_list[1][0][0]
        main_restore_args = mock_run.call_args_list[2][0][0]

        assert table_exists_args[0] == FAKE_PSQL
        assert "to_regclass" in table_exists_args[-1]
        assert wipe_args[0] == FAKE_PSQL
        assert main_restore_args[0] == FAKE_PG_RESTORE

    def test_run_restore_fails_when_database_ends_up_empty(self, backup_bucket):
        """Regression guard for the 2026-08-25 production incident: three
        consecutive automated restores reported success while every table
        was empty afterward - pg_restore itself exited 0, nothing caught
        it. A restore that leaves zero rows anywhere must now fail loudly
        instead of silently reporting success."""
        storage = _make_storage()
        backup_name = "test-2026-01-01_03-00-00.dump"
        _put_backup(storage, backup_name)

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, side_effect=_which_side_effect),
            patch(
                "subprocess.run",
                side_effect=[
                    MagicMock(returncode=0, stdout=b"f"),  # table-exists check: no
                    MagicMock(returncode=0),  # wipe
                    MagicMock(returncode=0),  # main restore
                    MagicMock(returncode=0),  # ANALYZE (restore verification)
                    MagicMock(returncode=0, stdout=b"0"),  # row count: EMPTY
                ],
            ),
            patch.object(Path, "unlink"),
            pytest.raises(RuntimeError, match="zero rows"),
        ):
            run_restore(storage, backup_name=backup_name)

    def test_run_restore_wipes_schema_before_restoring(self, backup_bucket):
        """Regression guard for the pg_restore --clean drop-order bug: a
        real production dump (members table with a self-referencing FK
        plus an external FK both depending on members_pkey) made
        pg_restore --clean fail to drop members_pkey and then silently
        continue past the error, leaving the schema missing that FK
        entirely - even on a run that reported zero errors. Wiping the
        schema first and restoring without --clean/--if-exists sidesteps
        the ordering problem completely."""
        storage = _make_storage()
        backup_name = "test-2026-01-01_03-00-00.dump"
        _put_backup(storage, backup_name)

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, side_effect=_which_side_effect),
            patch("subprocess.run") as mock_run,
            patch.object(Path, "unlink"),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=b"t"),  # table-exists check
                MagicMock(returncode=0, stdout=b"snapshot-bytes"),  # snapshot dump
                MagicMock(returncode=0),  # wipe
                MagicMock(returncode=0),  # main restore
                MagicMock(returncode=0),  # ANALYZE (restore verification)
                MagicMock(returncode=0, stdout=b"5"),  # row count (verification)
                MagicMock(returncode=0),  # local history restore
            ]
            run_restore(storage, backup_name=backup_name)

        assert mock_run.call_count == 7
        wipe_args = mock_run.call_args_list[2][0][0]
        restore_args = mock_run.call_args_list[3][0][0]

        assert wipe_args[0] == FAKE_PSQL
        assert "-c" in wipe_args
        wipe_sql = wipe_args[wipe_args.index("-c") + 1]
        assert "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" in wipe_sql
        # Regression guard for the 2026-08-08 incident: an unbounded wait
        # for a conflicting lock froze an entire dev stage (including
        # login) for 20+ minutes with no self-recovery.
        assert "lock_timeout" in wipe_sql
        # Regression guard for the follow-up: a lock_timeout alone still
        # raced against this app's own ambient traffic (session-refresh
        # polling etc.) and lost often enough to be unreliable. Other
        # sessions on this database must be terminated deterministically
        # before the wipe, not just waited out - except the scheduler's
        # advisory-lock connection, which never conflicts and must survive.
        assert "pg_terminate_backend" in wipe_sql
        assert "pg_try_advisory_lock" in wipe_sql
        assert restore_args[0] == FAKE_PG_RESTORE
        assert "--clean" not in restore_args
        assert "--if-exists" not in restore_args

    def test_run_restore_latest(self, backup_bucket):
        storage = _make_storage()
        _put_backup(storage, "test-2026-01-01_03-00-00.dump")
        _put_backup(storage, "test-2026-06-30_03-00-00.dump")

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, side_effect=_which_side_effect),
            patch("subprocess.run") as mock_run,
            patch.object(Path, "unlink"),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=b"t"),  # table-exists check
                MagicMock(returncode=0, stdout=b"snapshot-bytes"),  # snapshot dump
                MagicMock(returncode=0),  # wipe
                MagicMock(returncode=0),  # main restore
                MagicMock(returncode=0),  # ANALYZE (restore verification)
                MagicMock(returncode=0, stdout=b"5"),  # row count (verification)
                MagicMock(returncode=0),  # local history restore
            ]
            restored = run_restore(storage, backup_name=None)

        assert restored == "test-2026-06-30_03-00-00.dump"
        assert mock_run.call_count == 7

    def test_run_restore_latest_picks_newer_manual_over_older_scheduled(
        self, backup_bucket
    ):
        storage = _make_storage()
        _put_backup(storage, "test-2026-06-30_03-00-00.dump")
        newest = "test-2026-07-15_12-00-00-manual.dump"
        _put_backup(storage, newest)

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, side_effect=_which_side_effect),
            patch("subprocess.run") as mock_run,
            patch.object(Path, "unlink"),
            patch.object(storage, "download", wraps=storage.download) as mock_download,
        ):
            # stdout="1": not "t" (table-exists check -> skip snapshot) but
            # still a valid int (restore-verification row-count check).
            mock_run.return_value = MagicMock(returncode=0, stdout=b"1")
            restored = run_restore(storage, backup_name=None)

        assert restored == newest
        mock_download.assert_called_once_with(key=f"{S3_PATH_DB_BACKUPS}/{newest}")

    def test_run_restore_latest_picks_newer_scheduled_over_older_manual(
        self, backup_bucket
    ):
        storage = _make_storage()
        _put_backup(storage, "test-2026-06-30_03-00-00-manual.dump")
        newest = "test-2026-07-15_03-00-00.dump"
        _put_backup(storage, newest)

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, side_effect=_which_side_effect),
            patch("subprocess.run") as mock_run,
            patch.object(Path, "unlink"),
            patch.object(storage, "download", wraps=storage.download) as mock_download,
        ):
            # stdout="1": not "t" (table-exists check -> skip snapshot) but
            # still a valid int (restore-verification row-count check).
            mock_run.return_value = MagicMock(returncode=0, stdout=b"1")
            restored = run_restore(storage, backup_name=None)

        assert restored == newest
        mock_download.assert_called_once_with(key=f"{S3_PATH_DB_BACKUPS}/{newest}")

    def test_run_restore_no_backups(self, backup_bucket):
        storage = _make_storage()
        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            pytest.raises(RuntimeError, match="No backups found"),
        ):
            run_restore(storage, backup_name=None)

    def test_run_restore_not_postgres(self):
        storage = MagicMock(spec=StorageClient)
        with (
            patch.dict(os.environ, {"DATABASE_URL": SQLITE_URL}),
            pytest.raises(RuntimeError, match="Backup requires PostgreSQL"),
        ):
            run_restore(storage)

    def test_run_restore_production_without_force(self):
        storage = MagicMock(spec=StorageClient)
        with (
            patch.dict(
                os.environ,
                {"DATABASE_URL": PG_URL, "APP_ENVIRONMENT": "production"},
            ),
            pytest.raises(RuntimeError, match="force=True"),
        ):
            run_restore(storage)

    def test_run_restore_production_with_force(self, backup_bucket):
        storage = _make_storage()
        backup_name = "production-2026-01-01_03-00-00.dump"
        _put_backup(storage, backup_name)

        with (
            patch.dict(
                os.environ,
                {"DATABASE_URL": PG_URL, "APP_ENVIRONMENT": "production"},
            ),
            patch(PATCH_WHICH, side_effect=_which_side_effect),
            patch("subprocess.run") as mock_run,
            patch.object(Path, "unlink"),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=b"t"),  # table-exists check
                MagicMock(returncode=0, stdout=b"snapshot-bytes"),  # snapshot dump
                MagicMock(returncode=0),  # wipe
                MagicMock(returncode=0),  # main restore
                MagicMock(returncode=0),  # ANALYZE (restore verification)
                MagicMock(returncode=0, stdout=b"5"),  # row count (verification)
                MagicMock(returncode=0),  # local history restore
            ]
            run_restore(storage, backup_name=backup_name, force=True)

        assert mock_run.call_count == 7

    def test_run_restore_snapshot_dump_fails_cleans_up_tempfile(self, backup_bucket):
        storage = _make_storage()
        backup_name = "test-2026-01-01_03-00-00.dump"
        _put_backup(storage, backup_name)

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, side_effect=_which_side_effect),
            patch(
                "subprocess.run",
                side_effect=[
                    MagicMock(returncode=0, stdout=b"t"),  # table-exists check
                    CalledProcessError(
                        1, "pg_dump", stderr=b"pg_dump: error: could not connect"
                    ),
                ],
            ),
            patch.object(Path, "unlink") as mock_unlink,
            pytest.raises(RuntimeError, match="could not connect"),
        ):
            run_restore(storage, backup_name=backup_name)

        mock_unlink.assert_called_once()

    def test_run_restore_psql_wipe_fails_cleans_up_tempfile(self, backup_bucket):
        storage = _make_storage()
        backup_name = "test-2026-01-01_03-00-00.dump"
        _put_backup(storage, backup_name)

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, side_effect=_which_side_effect),
            patch(
                "subprocess.run",
                side_effect=[
                    MagicMock(returncode=0, stdout=b"t"),  # table-exists check
                    MagicMock(returncode=0, stdout=b"local-history-snapshot"),
                    CalledProcessError(
                        1, "psql", stderr=b"psql: error: could not connect"
                    ),
                ],
            ),
            patch.object(Path, "unlink") as mock_unlink,
            pytest.raises(RuntimeError, match="could not connect"),
        ):
            run_restore(storage, backup_name=backup_name)

        mock_unlink.assert_called_once()

    def test_run_restore_pg_restore_fails_cleans_up_tempfile(self, backup_bucket):
        storage = _make_storage()
        backup_name = "test-2026-01-01_03-00-00.dump"
        _put_backup(storage, backup_name)

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, side_effect=_which_side_effect),
            patch(
                "subprocess.run",
                side_effect=[
                    MagicMock(returncode=0, stdout=b"t"),  # table-exists check
                    MagicMock(returncode=0, stdout=b"local-history-snapshot"),
                    MagicMock(returncode=0),
                    CalledProcessError(
                        1, "pg_restore", stderr=b"pg_restore: error: could not connect"
                    ),
                ],
            ),
            patch.object(Path, "unlink") as mock_unlink,
            pytest.raises(RuntimeError, match="could not connect"),
        ):
            run_restore(storage, backup_name=backup_name)

        mock_unlink.assert_called_once()

    def test_run_restore_local_history_restore_fails_cleans_up_both_tempfiles(
        self, backup_bucket
    ):
        """The local-history re-insert (7th subprocess call) has its own
        temp file, separate from the main backup's temp file - both must
        be cleaned up even if only the later one fails."""
        storage = _make_storage()
        backup_name = "test-2026-01-01_03-00-00.dump"
        _put_backup(storage, backup_name)

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, side_effect=_which_side_effect),
            patch(
                "subprocess.run",
                side_effect=[
                    MagicMock(returncode=0, stdout=b"t"),  # table-exists check
                    MagicMock(returncode=0, stdout=b"local-history-snapshot"),
                    MagicMock(returncode=0),  # wipe
                    MagicMock(returncode=0),  # main restore
                    MagicMock(returncode=0),  # ANALYZE (restore verification)
                    MagicMock(returncode=0, stdout=b"5"),  # row count (verification)
                    CalledProcessError(
                        1, "pg_restore", stderr=b"pg_restore: error: could not connect"
                    ),
                ],
            ),
            patch.object(Path, "unlink") as mock_unlink,
            pytest.raises(RuntimeError, match="could not connect"),
        ):
            run_restore(storage, backup_name=backup_name)

        assert mock_unlink.call_count == 2


class TestRetryTransientS3:
    """Regression guard for the 2026-08-25 production incident: a restore's
    S3 list/download hit a transient "not found" for an object that
    demonstrably existed the whole time (unchanged S3 LastModified).
    Tested directly against the helper (not through run_restore()) since
    it's a small, genuinely reusable primitive - a direct unit test can
    assert exact attempt counts, which an integration test through the
    full restore flow can't do cleanly."""

    def test_succeeds_on_first_attempt(self):
        operation = MagicMock(return_value="ok")
        assert _retry_transient_s3(operation) == "ok"
        assert operation.call_count == 1

    def test_retries_then_succeeds(self):
        error = ClientError({"Error": {"Code": "SlowDown"}}, "ListObjectsV2")
        operation = MagicMock(side_effect=[error, "ok"])
        with patch("time.sleep"):
            assert _retry_transient_s3(operation, attempts=3) == "ok"
        assert operation.call_count == 2

    def test_gives_up_after_exhausting_attempts(self):
        error = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        operation = MagicMock(side_effect=[error, error, error])
        with patch("time.sleep"), pytest.raises(ClientError):
            _retry_transient_s3(operation, attempts=3)
        assert operation.call_count == 3


class TestCleanupOldBackups:
    def test_deletes_expired(self, backup_bucket):
        storage = _make_storage()
        old_name = "test-2020-01-01_03-00-00.dump"
        new_name = "test-2099-01-01_03-00-00.dump"
        _put_backup(storage, old_name)
        _put_backup(storage, new_name)

        deleted = cleanup_old_backups(storage, retention_days=30)

        assert old_name in deleted
        assert new_name not in deleted
        assert not storage.exists(f"{S3_PATH_DB_BACKUPS}/{old_name}")
        assert storage.exists(f"{S3_PATH_DB_BACKUPS}/{new_name}")

    def test_keeps_all_recent(self, backup_bucket):
        storage = _make_storage()
        name = "test-2099-12-31_03-00-00.dump"
        _put_backup(storage, name)

        deleted = cleanup_old_backups(storage, retention_days=30)

        assert deleted == []
        assert storage.exists(f"{S3_PATH_DB_BACKUPS}/{name}")

    def test_skips_unparseable_names(self, backup_bucket):
        storage = _make_storage()
        storage.upload(key=f"{S3_PATH_DB_BACKUPS}/garbage-file.txt", data=b"x")

        deleted = cleanup_old_backups(storage, retention_days=1)

        assert deleted == []
        assert storage.exists(f"{S3_PATH_DB_BACKUPS}/garbage-file.txt")

    def test_returns_deleted_names(self, backup_bucket):
        storage = _make_storage()
        old = "test-2020-06-01_00-00-00.dump"
        _put_backup(storage, old)

        deleted = cleanup_old_backups(storage, retention_days=1)

        assert deleted == [old]

    def test_manual_backups_are_not_skipped(self, backup_bucket):
        storage = _make_storage()
        old_manual = "test-2020-06-01_00-00-00-manual.dump"
        _put_backup(storage, old_manual)

        deleted = cleanup_old_backups(storage, retention_days=1)

        assert deleted == [old_manual]


class TestListKeys:
    def test_empty_prefix(self, backup_bucket):
        storage = _make_storage()
        assert storage.list_keys(prefix=f"{S3_PATH_DB_BACKUPS}/") == []

    def test_returns_all_keys(self, backup_bucket):
        storage = _make_storage()
        for i in range(3):
            key = f"{S3_PATH_DB_BACKUPS}/test-200{i}-01-01_00-00-00.dump"
            storage.upload(key=key, data=b"x")

        keys = storage.list_keys(prefix=f"{S3_PATH_DB_BACKUPS}/")
        assert len(keys) == 3
