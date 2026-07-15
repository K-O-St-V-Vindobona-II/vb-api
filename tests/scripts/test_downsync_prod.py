"""Regression tests for scripts/downsync_prod.py.

run_restore() itself is already covered by tests/test_backup_service.py and
the S3 mirror logic by tests/test_s3_mirror_service.py - these tests only
exercise this script's own orchestration (guard, confirmation prompt, step
skipping/ordering, alembic invocation, exit codes).
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

import scripts.downsync_prod as downsync_prod
from app.services.s3_mirror_service import MirrorResult

SECRETS = {
    "AWS_ACCESS_KEY_ID": "key",
    "AWS_SECRET_ACCESS_KEY": "secret",
    "AWS_BUCKET": "vindobona2-at",
    "AWS_REGION": "eu-central-1",
}


def _run_main(argv: list[str]) -> None:
    with patch("sys.argv", ["downsync_prod.py", *argv]):
        downsync_prod.main()


class TestProductionGuard:
    def test_blocks_before_anything_else(self) -> None:
        with (
            patch.object(downsync_prod, "APP_ENVIRONMENT", "production"),
            patch.object(downsync_prod, "_load_aws_secrets") as mock_secrets,
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main([])

        assert exc_info.value.code == 1
        mock_secrets.assert_not_called()


class TestConfirmation:
    def test_aborts_when_confirmation_is_not_yes(self) -> None:
        with (
            patch.object(downsync_prod, "APP_ENVIRONMENT", "development"),
            patch.object(downsync_prod, "_load_aws_secrets", return_value=SECRETS),
            patch("builtins.input", return_value="no"),
            patch.object(downsync_prod, "_run_db_restore") as mock_db,
            patch.object(downsync_prod, "_run_s3_mirror") as mock_s3,
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main([])

        assert exc_info.value.code == 0
        mock_db.assert_not_called()
        mock_s3.assert_not_called()

    def test_yes_flag_skips_prompt(self) -> None:
        with (
            patch.object(downsync_prod, "APP_ENVIRONMENT", "development"),
            patch.object(downsync_prod, "_load_aws_secrets", return_value=SECRETS),
            patch("builtins.input") as mock_input,
            patch.object(
                downsync_prod, "_build_prod_storage", return_value=MagicMock()
            ),
            patch.object(downsync_prod, "_run_db_restore"),
            patch.object(downsync_prod, "_run_s3_mirror", return_value=MirrorResult()),
        ):
            _run_main(["--yes"])

        mock_input.assert_not_called()

    def test_dry_run_skips_prompt(self) -> None:
        with (
            patch.object(downsync_prod, "APP_ENVIRONMENT", "development"),
            patch.object(downsync_prod, "_load_aws_secrets", return_value=SECRETS),
            patch("builtins.input") as mock_input,
            patch.object(
                downsync_prod, "_build_prod_storage", return_value=MagicMock()
            ),
            patch.object(downsync_prod, "_run_db_restore"),
            patch.object(downsync_prod, "_run_s3_mirror", return_value=MirrorResult()),
        ):
            _run_main(["--dry-run"])

        mock_input.assert_not_called()


class TestStepSkipping:
    def test_skip_db_only_runs_s3(self) -> None:
        with (
            patch.object(downsync_prod, "APP_ENVIRONMENT", "development"),
            patch.object(downsync_prod, "_load_aws_secrets", return_value=SECRETS),
            patch.object(
                downsync_prod, "_build_prod_storage", return_value=MagicMock()
            ) as mock_build_storage,
            patch.object(downsync_prod, "_run_db_restore") as mock_db,
            patch.object(
                downsync_prod, "_run_s3_mirror", return_value=MirrorResult()
            ) as mock_s3,
        ):
            _run_main(["--yes", "--skip-db"])

        mock_build_storage.assert_called_once()
        mock_db.assert_not_called()
        mock_s3.assert_called_once()
        assert mock_s3.call_args.args[2:] == (False, False)

    def test_skip_s3_only_runs_db(self) -> None:
        with (
            patch.object(downsync_prod, "APP_ENVIRONMENT", "development"),
            patch.object(downsync_prod, "_load_aws_secrets", return_value=SECRETS),
            patch.object(
                downsync_prod, "_build_prod_storage", return_value=MagicMock()
            ) as mock_build_storage,
            patch.object(downsync_prod, "_run_db_restore") as mock_db,
            patch.object(
                downsync_prod, "_run_s3_mirror", return_value=MirrorResult()
            ) as mock_s3,
        ):
            _run_main(["--yes", "--skip-s3"])

        # The DB step reads only from local storage — no need to load
        # prod AWS credentials at all when the S3 step is skipped.
        mock_build_storage.assert_not_called()
        mock_db.assert_called_once()
        mock_s3.assert_not_called()

    def test_no_delete_is_threaded_through_to_s3_step(self) -> None:
        with (
            patch.object(downsync_prod, "APP_ENVIRONMENT", "development"),
            patch.object(downsync_prod, "_load_aws_secrets", return_value=SECRETS),
            patch.object(
                downsync_prod, "_build_prod_storage", return_value=MagicMock()
            ),
            patch.object(
                downsync_prod, "_run_s3_mirror", return_value=MirrorResult()
            ) as mock_s3,
        ):
            _run_main(["--yes", "--skip-db", "--no-delete"])

        assert mock_s3.call_args.args[2:] == (False, True)

    def test_s3_errors_abort_before_db_step(self) -> None:
        """The S3 mirror must run before the DB restore (the DB step reads
        from local MinIO, which the mirror step just populated) — if the
        mirror reports errors, the DB step must not run against
        potentially-incomplete local data."""
        with (
            patch.object(downsync_prod, "APP_ENVIRONMENT", "development"),
            patch.object(downsync_prod, "_load_aws_secrets", return_value=SECRETS),
            patch.object(
                downsync_prod, "_build_prod_storage", return_value=MagicMock()
            ),
            patch.object(
                downsync_prod,
                "_run_s3_mirror",
                return_value=MirrorResult(errors=["archive/store/x"]),
            ),
            patch.object(downsync_prod, "_run_db_restore") as mock_db,
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["--yes"])

        assert exc_info.value.code == 1
        mock_db.assert_not_called()


class TestRunDbRestore:
    def test_dry_run_only_lists_latest_backup(self) -> None:
        storage = MagicMock()
        storage.list_keys.return_value = ["db-backups/a.dump", "db-backups/b.dump"]

        with patch.object(downsync_prod, "run_restore") as mock_restore:
            downsync_prod._run_db_restore(storage, dry_run=True)

        mock_restore.assert_not_called()

    def test_dry_run_with_no_backups_does_not_crash(self) -> None:
        storage = MagicMock()
        storage.list_keys.return_value = []

        with patch.object(downsync_prod, "run_restore") as mock_restore:
            downsync_prod._run_db_restore(storage, dry_run=True)

        mock_restore.assert_not_called()

    def test_restore_reads_only_local_storage(self) -> None:
        """Regression guard: the DB step must never touch prod directly -
        it only ever receives the local StorageClient, since it relies on
        the S3 mirror step having already brought prod's latest backup
        down into local MinIO."""
        local_storage = MagicMock()
        completed = subprocess.CompletedProcess(args=[], returncode=0)

        with (
            patch.object(downsync_prod, "run_restore") as mock_restore,
            patch("subprocess.run", return_value=completed),
        ):
            downsync_prod._run_db_restore(local_storage, dry_run=False)

        mock_restore.assert_called_once_with(local_storage)

    def test_restore_failure_exits_one_without_running_alembic(self) -> None:
        storage = MagicMock()

        with (
            patch.object(
                downsync_prod, "run_restore", side_effect=RuntimeError("boom")
            ),
            patch("subprocess.run") as mock_subprocess,
            pytest.raises(SystemExit) as exc_info,
        ):
            downsync_prod._run_db_restore(storage, dry_run=False)

        assert exc_info.value.code == 1
        mock_subprocess.assert_not_called()

    def test_successful_restore_runs_alembic_upgrade(self) -> None:
        storage = MagicMock()
        completed = subprocess.CompletedProcess(args=[], returncode=0)

        with (
            patch.object(downsync_prod, "run_restore"),
            patch("subprocess.run", return_value=completed) as mock_subprocess,
        ):
            downsync_prod._run_db_restore(storage, dry_run=False)

        mock_subprocess.assert_called_once()
        assert mock_subprocess.call_args.args[0] == ["alembic", "upgrade", "head"]

    def test_alembic_failure_exits_one(self) -> None:
        storage = MagicMock()
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stderr=b"migration error"
        )

        with (
            patch.object(downsync_prod, "run_restore"),
            patch("subprocess.run", return_value=completed),
            pytest.raises(SystemExit) as exc_info,
        ):
            downsync_prod._run_db_restore(storage, dry_run=False)

        assert exc_info.value.code == 1


class TestRunS3Mirror:
    def test_calls_mirror_prefix_with_expected_args(self) -> None:
        prod_storage = MagicMock()
        local_storage = MagicMock()

        with patch.object(
            downsync_prod,
            "mirror_prefix",
            return_value=MirrorResult(synced=["a"], skipped=1),
        ) as mock_mirror:
            result = downsync_prod._run_s3_mirror(
                prod_storage, local_storage, dry_run=True, no_delete=False
            )

        mock_mirror.assert_called_once_with(
            prod_storage,
            local_storage,
            dry_run=True,
            delete_orphans=True,
            on_progress=downsync_prod._print_mirror_progress,
        )
        assert result.synced == ["a"]

    def test_no_delete_maps_to_delete_orphans_false(self) -> None:
        with patch.object(
            downsync_prod, "mirror_prefix", return_value=MirrorResult()
        ) as mock_mirror:
            downsync_prod._run_s3_mirror(
                MagicMock(), MagicMock(), dry_run=False, no_delete=True
            )

        assert mock_mirror.call_args.kwargs["delete_orphans"] is False


class TestLoadAwsSecrets:
    def test_missing_access_key_exits_one(self) -> None:
        with (
            patch.object(downsync_prod, "_load_env_file", return_value={}),
            pytest.raises(SystemExit) as exc_info,
        ):
            downsync_prod._load_aws_secrets()

        assert exc_info.value.code == 1

    def test_missing_bucket_exits_one(self) -> None:
        with (
            patch.object(
                downsync_prod,
                "_load_env_file",
                return_value={"AWS_ACCESS_KEY_ID": "key"},
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            downsync_prod._load_aws_secrets()

        assert exc_info.value.code == 1

    def test_returns_secrets_when_complete(self) -> None:
        with patch.object(downsync_prod, "_load_env_file", return_value=SECRETS):
            result = downsync_prod._load_aws_secrets()

        assert result == SECRETS
