"""Regression tests for scripts/backup_db.py.

run_backup/cleanup_old_backups themselves are already covered by
tests/test_backup_service.py — these tests only exercise the CLI wrapper's
own logic (argument parsing, control flow, exit codes).
"""

from unittest.mock import MagicMock, patch

import pytest

import scripts.backup_db as backup_db


def _run_main(argv: list[str]) -> None:
    with patch("sys.argv", ["backup_db.py", *argv]):
        backup_db.main()


def test_list_prints_sorted_keys_without_creating_backup(capsys) -> None:
    mock_storage = MagicMock()
    mock_storage.list_keys.return_value = ["db-backups/b.dump", "db-backups/a.dump"]

    with (
        patch.object(backup_db, "get_storage", return_value=mock_storage),
        patch.object(backup_db, "run_backup") as mock_run_backup,
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["--list"])

    assert exc_info.value.code == 0
    mock_run_backup.assert_not_called()
    out = capsys.readouterr().out
    assert out.splitlines() == ["db-backups/a.dump", "db-backups/b.dump"]


def test_list_with_no_backups_prints_to_stderr_and_exits_zero(capsys) -> None:
    mock_storage = MagicMock()
    mock_storage.list_keys.return_value = []

    with (
        patch.object(backup_db, "get_storage", return_value=mock_storage),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["--list"])

    assert exc_info.value.code == 0
    assert "No backups found." in capsys.readouterr().err


def test_default_run_creates_backup_without_cleanup(capsys) -> None:
    mock_storage = MagicMock()

    with (
        patch.object(backup_db, "get_storage", return_value=mock_storage),
        patch.object(
            backup_db, "run_backup", return_value="production-2026-07-02_12-00-00.dump"
        ) as mock_run_backup,
        patch.object(backup_db, "cleanup_old_backups") as mock_cleanup,
    ):
        _run_main([])

    mock_run_backup.assert_called_once_with(mock_storage, manual=True)
    mock_cleanup.assert_not_called()
    assert "production-2026-07-02_12-00-00.dump" in capsys.readouterr().out


def test_cleanup_flag_runs_cleanup_and_reports_deleted(capsys) -> None:
    mock_storage = MagicMock()

    with (
        patch.object(backup_db, "get_storage", return_value=mock_storage),
        patch.object(backup_db, "run_backup", return_value="new-backup.dump"),
        patch.object(
            backup_db, "cleanup_old_backups", return_value=["old1.dump", "old2.dump"]
        ) as mock_cleanup,
    ):
        _run_main(["--cleanup"])

    mock_cleanup.assert_called_once_with(mock_storage)
    out = capsys.readouterr().out
    assert "Cleaned up 2 expired backup(s):" in out
    assert "old1.dump" in out
    assert "old2.dump" in out


def test_cleanup_flag_with_nothing_to_delete(capsys) -> None:
    mock_storage = MagicMock()

    with (
        patch.object(backup_db, "get_storage", return_value=mock_storage),
        patch.object(backup_db, "run_backup", return_value="new-backup.dump"),
        patch.object(backup_db, "cleanup_old_backups", return_value=[]),
    ):
        _run_main(["--cleanup"])

    assert "No expired backups to clean up." in capsys.readouterr().out


def test_run_backup_runtime_error_exits_one(capsys) -> None:
    mock_storage = MagicMock()

    with (
        patch.object(backup_db, "get_storage", return_value=mock_storage),
        patch.object(
            backup_db,
            "run_backup",
            side_effect=RuntimeError("Backup requires PostgreSQL. Got: sqlite..."),
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main([])

    assert exc_info.value.code == 1
    assert "ERROR: Backup requires PostgreSQL" in capsys.readouterr().err
