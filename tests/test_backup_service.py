"""Tests for backup_service — run_backup, run_restore, cleanup_old_backups."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import MagicMock, patch

import boto3
import pytest

from app.core.storage import S3_PATH_DB_BACKUPS, StorageClient
from app.services.backup_service import (
    _parse_backup_timestamp,
    _parse_db_url,
    cleanup_old_backups,
    run_backup,
    run_restore,
)

BACKUP_BUCKET = "backup-tests"
PG_URL = "postgresql://user:secret@localhost:5432/testdb"
SQLITE_URL = "sqlite:///test.db"
FAKE_PG_DUMP = "/usr/bin/pg_dump"
FAKE_PG_RESTORE = "/usr/bin/pg_restore"
PATCH_WHICH = "app.services.backup_service.shutil.which"

_S3_CREDS = dict(
    region_name="us-east-1",
    aws_access_key_id="testing",
    aws_secret_access_key="testing",
)


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
        assert dt == datetime(2026, 6, 30, 3, 0, 0, tzinfo=UTC)

    def test_valid_production(self):
        dt = _parse_backup_timestamp("production-2025-01-15_22-30-45.dump")
        assert dt == datetime(2025, 1, 15, 22, 30, 45, tzinfo=UTC)

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
            patch("subprocess.run", side_effect=CalledProcessError(1, "pg_dump")),
            pytest.raises(CalledProcessError),
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


class TestRunRestore:
    def test_run_restore_specific_name(self, backup_bucket):
        storage = _make_storage()
        backup_name = "test-2026-01-01_03-00-00.dump"
        _put_backup(storage, backup_name)

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, return_value=FAKE_PG_RESTORE),
            patch("subprocess.run") as mock_run,
            patch.object(Path, "unlink"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            run_restore(storage, backup_name=backup_name)

        args = mock_run.call_args[0][0]
        assert "pg_restore" in args[0]

    def test_run_restore_latest(self, backup_bucket):
        storage = _make_storage()
        _put_backup(storage, "test-2026-01-01_03-00-00.dump")
        _put_backup(storage, "test-2026-06-30_03-00-00.dump")

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, return_value=FAKE_PG_RESTORE),
            patch("subprocess.run") as mock_run,
            patch.object(Path, "unlink"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            run_restore(storage, backup_name=None)

        mock_run.assert_called_once()

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
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch("app.services.backup_service.APP_ENVIRONMENT", "production"),
            pytest.raises(RuntimeError, match="force=True"),
        ):
            run_restore(storage)

    def test_run_restore_production_with_force(self, backup_bucket):
        storage = _make_storage()
        backup_name = "production-2026-01-01_03-00-00.dump"
        _put_backup(storage, backup_name)

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch("app.services.backup_service.APP_ENVIRONMENT", "production"),
            patch(PATCH_WHICH, return_value=FAKE_PG_RESTORE),
            patch("subprocess.run") as mock_run,
            patch.object(Path, "unlink"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            run_restore(storage, backup_name=backup_name, force=True)

        mock_run.assert_called_once()

    def test_run_restore_pg_restore_fails_cleans_up_tempfile(self, backup_bucket):
        storage = _make_storage()
        backup_name = "test-2026-01-01_03-00-00.dump"
        _put_backup(storage, backup_name)

        with (
            patch.dict(os.environ, {"DATABASE_URL": PG_URL}),
            patch(PATCH_WHICH, return_value=FAKE_PG_RESTORE),
            patch("subprocess.run", side_effect=CalledProcessError(1, "pg_restore")),
            patch.object(Path, "unlink") as mock_unlink,
            pytest.raises(CalledProcessError),
        ):
            run_restore(storage, backup_name=backup_name)

        mock_unlink.assert_called_once()


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


class TestNextBackupRun:
    def test_future_hour_today(self):
        from app.core.scheduler import _next_backup_run

        with patch("app.core.scheduler.datetime") as mock_dt:
            now = datetime(2026, 6, 30, 1, 0, 0, tzinfo=UTC)
            mock_dt.now.return_value = now
            result = _next_backup_run(hour=3)

        assert result.date() == now.date()
        assert result.hour == 3

    def test_past_hour_gives_tomorrow(self):
        from app.core.scheduler import _next_backup_run

        with patch("app.core.scheduler.datetime") as mock_dt:
            now = datetime(2026, 6, 30, 5, 0, 0, tzinfo=UTC)
            mock_dt.now.return_value = now
            result = _next_backup_run(hour=3)

        assert result.date() == (now + timedelta(days=1)).date()
        assert result.hour == 3
