"""Regression tests for scripts/purge_deleted_archive_files.py.

purge_file()/list_deleted_files() themselves are already covered by
tests/test_archive_purge_service.py — these tests only exercise the CLI
wrapper's own logic (subcommand parsing, purgeability check, confirmation
prompt, exit codes).
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

import scripts.purge_deleted_archive_files as purge_script
from app.services.archive_purge_service import PurgeCandidate, PurgeError, PurgeResult
from tests.scripts._subprocess_helpers import (
    assert_module_imports_and_configures_mappers,
)


def _run_main(argv: list[str]) -> None:
    with patch("sys.argv", ["purge_deleted_archive_files.py", *argv]):
        purge_script.main()


def _candidate(
    file_id: int = 1, path: str = "Fotos", filename: str = "gruppenfoto.jpg"
) -> PurgeCandidate:
    return PurgeCandidate(
        file_id=file_id,
        path=path,
        filename=filename,
        description="Gruppenfoto",
        deleted_at=datetime(2026, 7, 20, 14, 32, 10, tzinfo=UTC),
        size=100,
        sha256_hash="a" * 64,
        created_by="Max Muster",
    )


def test_standalone_import_configures_mappers_without_error() -> None:
    assert_module_imports_and_configures_mappers("scripts.purge_deleted_archive_files")


def test_no_command_prints_help_without_touching_db(capsys) -> None:
    with (
        patch.object(purge_script, "SessionLocal") as mock_session_local,
        patch.object(purge_script, "list_deleted_files") as mock_list_deleted_files,
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main([])

    assert exc_info.value.code == 0
    mock_session_local.assert_not_called()
    mock_list_deleted_files.assert_not_called()
    assert "usage:" in capsys.readouterr().out


def test_unknown_command_is_rejected_by_argparse() -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_main(["bogus"])

    assert exc_info.value.code == 2


def test_list_prints_candidates_without_prompting_and_exits_zero(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[_candidate()]),
        patch.object(purge_script, "purge_file") as mock_purge_file,
        patch("builtins.input") as mock_input,
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["list"])

    assert exc_info.value.code == 0
    mock_input.assert_not_called()
    mock_purge_file.assert_not_called()
    out = capsys.readouterr().out
    assert "Fotos" in out
    assert "gruppenfoto.jpg" in out
    assert "2026-07-20" in out
    mock_db.close.assert_called_once()


def test_list_with_no_candidates(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[]),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["list"])

    assert exc_info.value.code == 0
    assert "No soft-deleted archive files found." in capsys.readouterr().out


def test_purge_without_id_is_rejected_by_argparse() -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_main(["purge"])

    assert exc_info.value.code == 2


def test_purge_with_non_integer_id_is_rejected_by_argparse() -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_main(["purge", "abc"])

    assert exc_info.value.code == 2


def test_purge_with_id_not_in_list_errors_without_prompting(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[_candidate(1)]),
        patch.object(purge_script, "purge_file") as mock_purge_file,
        patch("builtins.input") as mock_input,
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["purge", "999"])

    assert exc_info.value.code == 1
    mock_input.assert_not_called()
    mock_purge_file.assert_not_called()
    assert "999" in capsys.readouterr().err


def test_purge_aborts_on_non_yes_answer(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[_candidate(1)]),
        patch.object(purge_script, "purge_file") as mock_purge_file,
        patch("builtins.input", return_value="no"),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["purge", "1"])

    assert exc_info.value.code == 0
    mock_purge_file.assert_not_called()
    assert "Aborted." in capsys.readouterr().out


def test_purge_with_matching_id_prompts_and_purges_on_yes() -> None:
    mock_db = MagicMock()
    candidates = [_candidate(file_id=1), _candidate(file_id=2)]
    result = PurgeResult(
        file_id=2, store_item_deleted=True, s3_keys_deleted=["k"], s3_errors=[]
    )

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=candidates),
        patch.object(purge_script, "get_storage", return_value=MagicMock()),
        patch.object(
            purge_script, "purge_file", return_value=result
        ) as mock_purge_file,
        patch("builtins.input", return_value="yes") as mock_input,
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["purge", "2"])

    assert exc_info.value.code == 0
    mock_input.assert_called_once()
    mock_purge_file.assert_called_once()
    call_args = mock_purge_file.call_args[0]
    assert call_args[0] is mock_db
    assert call_args[2] == 2
    mock_db.close.assert_called_once()


def test_purge_error_is_reported_and_exit_code_one(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[_candidate(1)]),
        patch.object(purge_script, "get_storage", return_value=MagicMock()),
        patch.object(purge_script, "purge_file", side_effect=PurgeError("boom")),
        patch("builtins.input", return_value="yes"),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["purge", "1"])

    assert exc_info.value.code == 1
    assert "boom" in capsys.readouterr().err


def test_purge_s3_errors_are_reported_as_warnings_exit_code_one(capsys) -> None:
    mock_db = MagicMock()
    result = PurgeResult(
        file_id=1,
        store_item_deleted=True,
        s3_keys_deleted=[],
        s3_errors=["archive/store/abc: boom"],
    )

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[_candidate(1)]),
        patch.object(purge_script, "get_storage", return_value=MagicMock()),
        patch.object(purge_script, "purge_file", return_value=result),
        patch("builtins.input", return_value="yes"),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["purge", "1"])

    assert exc_info.value.code == 1
    assert "archive/store/abc: boom" in capsys.readouterr().err


def test_purge_success_exit_code_zero() -> None:
    mock_db = MagicMock()
    result = PurgeResult(
        file_id=1, store_item_deleted=True, s3_keys_deleted=[], s3_errors=[]
    )

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[_candidate(1)]),
        patch.object(purge_script, "get_storage", return_value=MagicMock()),
        patch.object(purge_script, "purge_file", return_value=result),
        patch("builtins.input", return_value="yes"),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["purge", "1"])

    assert exc_info.value.code == 0
