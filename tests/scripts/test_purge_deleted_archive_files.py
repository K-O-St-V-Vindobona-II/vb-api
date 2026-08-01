"""Regression tests for scripts/purge_deleted_archive_files.py.

purge_file()/list_deleted_files() themselves are already covered by
tests/test_archive_purge_service.py — these tests only exercise the CLI
wrapper's own logic (argument parsing, control flow, confirmation prompt,
exit codes).
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


def _candidate(file_id: int = 1, path: str = "Fotos") -> PurgeCandidate:
    return PurgeCandidate(
        file_id=file_id,
        path=path,
        description="Gruppenfoto",
        deleted_at=datetime(2026, 7, 20, 14, 32, 10, tzinfo=UTC),
        size=100,
        sha256_hash="a" * 64,
        created_by="Max Muster",
    )


def test_standalone_import_configures_mappers_without_error() -> None:
    assert_module_imports_and_configures_mappers("scripts.purge_deleted_archive_files")


def test_list_flag_prints_candidates_without_prompting_and_exits_zero(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[_candidate()]),
        patch.object(purge_script, "purge_file") as mock_purge_file,
        patch("builtins.input") as mock_input,
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["--list"])

    assert exc_info.value.code == 0
    mock_input.assert_not_called()
    mock_purge_file.assert_not_called()
    out = capsys.readouterr().out
    assert "Fotos" in out
    assert "2026-07-20" in out
    mock_db.close.assert_called_once()


def test_list_flag_with_no_candidates(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[]),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["--list"])

    assert exc_info.value.code == 0
    assert "No soft-deleted archive files found." in capsys.readouterr().out


def test_dry_run_shows_would_purge_without_calling_purge_file(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[_candidate()]),
        patch.object(purge_script, "purge_file") as mock_purge_file,
        patch("builtins.input") as mock_input,
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["--dry-run"])

    assert exc_info.value.code == 0
    mock_input.assert_not_called()
    mock_purge_file.assert_not_called()
    assert "WOULD PURGE: file 1" in capsys.readouterr().out


def test_default_run_aborts_on_non_yes_answer(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[_candidate()]),
        patch.object(purge_script, "purge_file") as mock_purge_file,
        patch("builtins.input", return_value="no"),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main([])

    assert exc_info.value.code == 0
    mock_purge_file.assert_not_called()
    assert "Aborted." in capsys.readouterr().out


def test_default_run_purges_all_candidates_after_confirmation() -> None:
    mock_db = MagicMock()
    candidates = [_candidate(file_id=1), _candidate(file_id=2)]
    result = PurgeResult(
        file_id=1, store_item_deleted=True, s3_keys_deleted=["k"], s3_errors=[]
    )

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=candidates),
        patch.object(purge_script, "get_storage", return_value=MagicMock()),
        patch.object(
            purge_script, "purge_file", return_value=result
        ) as mock_purge_file,
        patch("builtins.input", return_value="yes"),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main([])

    assert exc_info.value.code == 0
    assert mock_purge_file.call_count == 2


def test_yes_flag_skips_prompt() -> None:
    mock_db = MagicMock()
    result = PurgeResult(
        file_id=1, store_item_deleted=True, s3_keys_deleted=[], s3_errors=[]
    )

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[_candidate()]),
        patch.object(purge_script, "get_storage", return_value=MagicMock()),
        patch.object(purge_script, "purge_file", return_value=result),
        patch("builtins.input") as mock_input,
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["--yes"])

    assert exc_info.value.code == 0
    mock_input.assert_not_called()


def test_file_id_flag_restricts_target_set() -> None:
    mock_db = MagicMock()
    candidates = [_candidate(file_id=1), _candidate(file_id=2)]
    result = PurgeResult(
        file_id=2, store_item_deleted=True, s3_keys_deleted=[], s3_errors=[]
    )

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=candidates),
        patch.object(purge_script, "get_storage", return_value=MagicMock()),
        patch.object(
            purge_script, "purge_file", return_value=result
        ) as mock_purge_file,
        patch("builtins.input", return_value="yes"),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["--file-id", "2"])

    assert exc_info.value.code == 0
    assert mock_purge_file.call_count == 1
    call_args = mock_purge_file.call_args[0]
    assert call_args[0] is mock_db
    assert call_args[2] == 2


def test_file_id_not_currently_deleted_warns_and_is_excluded_exit_code_one(
    capsys,
) -> None:
    mock_db = MagicMock()

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[_candidate()]),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["--file-id", "999", "--list"])

    assert exc_info.value.code == 1
    assert "999" in capsys.readouterr().err


def test_purge_error_for_one_file_does_not_abort_batch(capsys) -> None:
    mock_db = MagicMock()
    candidates = [_candidate(file_id=1), _candidate(file_id=2)]

    def fake_purge_file(_db: object, _storage: object, file_id: int) -> PurgeResult:
        if file_id == 1:
            msg = "boom"
            raise PurgeError(msg)
        return PurgeResult(
            file_id=2, store_item_deleted=False, s3_keys_deleted=[], s3_errors=[]
        )

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=candidates),
        patch.object(purge_script, "get_storage", return_value=MagicMock()),
        patch.object(
            purge_script, "purge_file", side_effect=fake_purge_file
        ) as mock_purge_file,
        patch("builtins.input", return_value="yes"),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main([])

    assert exc_info.value.code == 1
    assert mock_purge_file.call_count == 2
    assert "boom" in capsys.readouterr().err


def test_exit_code_zero_when_all_purged_successfully() -> None:
    mock_db = MagicMock()
    result = PurgeResult(
        file_id=1, store_item_deleted=True, s3_keys_deleted=[], s3_errors=[]
    )

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[_candidate()]),
        patch.object(purge_script, "get_storage", return_value=MagicMock()),
        patch.object(purge_script, "purge_file", return_value=result),
        patch("builtins.input", return_value="yes"),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main([])

    assert exc_info.value.code == 0


def test_prints_s3_errors_as_warnings_and_marks_had_errors(capsys) -> None:
    mock_db = MagicMock()
    result = PurgeResult(
        file_id=1,
        store_item_deleted=True,
        s3_keys_deleted=[],
        s3_errors=["archive/store/abc: boom"],
    )

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[_candidate()]),
        patch.object(purge_script, "get_storage", return_value=MagicMock()),
        patch.object(purge_script, "purge_file", return_value=result),
        patch("builtins.input", return_value="yes"),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main([])

    assert exc_info.value.code == 1
    assert "archive/store/abc: boom" in capsys.readouterr().err
