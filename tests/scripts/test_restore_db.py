"""Regression tests for scripts/restore_db.py.

run_restore itself is already covered by tests/test_backup_service.py —
these tests only exercise the CLI wrapper's own logic (argument parsing,
control flow, exit codes).
"""

from unittest.mock import MagicMock, patch

import pytest

import scripts.restore_db as restore_db


def _run_main(argv: list[str]) -> None:
    with patch("sys.argv", ["restore_db.py", *argv]):
        restore_db.main()


def test_list_prints_sorted_keys_without_restoring(capsys) -> None:
    mock_storage = MagicMock()
    mock_storage.list_keys.return_value = [
        "db-backups/b.dump",
        "db-backups/a.dump",
    ]

    with (
        patch.object(restore_db, "get_storage", return_value=mock_storage),
        patch.object(restore_db, "run_restore") as mock_run_restore,
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["--list"])

    assert exc_info.value.code == 0
    mock_run_restore.assert_not_called()
    out = capsys.readouterr().out
    assert out.splitlines() == ["db-backups/a.dump", "db-backups/b.dump"]


def test_list_with_no_backups_prints_to_stderr_and_exits_zero(capsys) -> None:
    mock_storage = MagicMock()
    mock_storage.list_keys.return_value = []

    with (
        patch.object(restore_db, "get_storage", return_value=mock_storage),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["--list"])

    assert exc_info.value.code == 0
    assert "No backups found." in capsys.readouterr().err


def test_default_run_restores_latest_without_force(capsys) -> None:
    mock_storage = MagicMock()

    with (
        patch.object(restore_db, "get_storage", return_value=mock_storage),
        patch.object(restore_db, "run_restore") as mock_run_restore,
    ):
        _run_main([])

    mock_run_restore.assert_called_once_with(
        mock_storage, backup_name=None, force=False
    )
    assert "Restore complete." in capsys.readouterr().out


def test_backup_name_flag_is_passed_through(capsys) -> None:
    mock_storage = MagicMock()

    with (
        patch.object(restore_db, "get_storage", return_value=mock_storage),
        patch.object(restore_db, "run_restore") as mock_run_restore,
    ):
        _run_main(["--backup-name", "production-2026-07-02_12-00-00.dump"])

    mock_run_restore.assert_called_once_with(
        mock_storage,
        backup_name="production-2026-07-02_12-00-00.dump",
        force=False,
    )
    assert "Restore complete." in capsys.readouterr().out


def test_force_flag_is_passed_through(capsys) -> None:
    mock_storage = MagicMock()

    with (
        patch.object(restore_db, "get_storage", return_value=mock_storage),
        patch.object(restore_db, "run_restore") as mock_run_restore,
    ):
        _run_main(["--force"])

    mock_run_restore.assert_called_once_with(mock_storage, backup_name=None, force=True)
    assert "Restore complete." in capsys.readouterr().out


def test_run_restore_runtime_error_exits_one(capsys) -> None:
    mock_storage = MagicMock()

    with (
        patch.object(restore_db, "get_storage", return_value=mock_storage),
        patch.object(
            restore_db,
            "run_restore",
            side_effect=RuntimeError(
                "Restore in production requires explicit force=True."
            ),
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main([])

    assert exc_info.value.code == 1
    assert (
        "ERROR: Restore in production requires explicit force=True."
        in capsys.readouterr().err
    )
