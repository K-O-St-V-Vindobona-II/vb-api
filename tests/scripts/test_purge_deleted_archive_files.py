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
    file_id: int = 1,
    path: str = "Fotos",
    filename: str = "gruppenfoto.jpg",
    active_sibling_count: int = 0,
    other_deleted_sibling_count: int = 0,
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
        archive_store_item_id=file_id,
        active_sibling_count=active_sibling_count,
        other_deleted_sibling_count=other_deleted_sibling_count,
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


def test_list_shows_impact_column_and_summary(capsys) -> None:
    mock_db = MagicMock()
    candidates = [
        _candidate(file_id=1, active_sibling_count=1),
        _candidate(file_id=2, other_deleted_sibling_count=1),
        _candidate(file_id=3),
    ]

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=candidates),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["list"])

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "duplicate" in out
    assert "shared" in out
    assert "SOLE" in out
    assert "1 duplicate" in out
    assert "1 shared" in out
    assert "1 sole reference" in out


def test_purge_confirm_shows_duplicate_note(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(
            purge_script,
            "list_deleted_files",
            return_value=[_candidate(1, active_sibling_count=2)],
        ),
        patch.object(purge_script, "purge_file") as mock_purge_file,
        patch("builtins.input", return_value="no"),
        pytest.raises(SystemExit),
    ):
        _run_main(["purge", "1"])

    out = capsys.readouterr().out
    assert "2 active file(s)" in out
    assert "will NOT be deleted" in out
    mock_purge_file.assert_not_called()


def test_purge_confirm_shows_shared_note(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(
            purge_script,
            "list_deleted_files",
            return_value=[_candidate(1, other_deleted_sibling_count=3)],
        ),
        patch("builtins.input", return_value="no"),
        pytest.raises(SystemExit),
    ):
        _run_main(["purge", "1"])

    out = capsys.readouterr().out
    assert "3 other" in out
    assert "removed once the last referencing file is purged" in out


def test_purge_confirm_shows_sole_warning(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(purge_script, "SessionLocal", return_value=mock_db),
        patch.object(purge_script, "list_deleted_files", return_value=[_candidate(1)]),
        patch("builtins.input", return_value="no"),
        pytest.raises(SystemExit),
    ):
        _run_main(["purge", "1"])

    out = capsys.readouterr().out
    assert "WARNING: this is the ONLY reference" in out


class TestPurgeDuplicatesCommand:
    def test_no_dir_id_is_rejected_by_argparse(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _run_main(["purge-duplicates"])

        assert exc_info.value.code == 2

    def test_non_integer_dir_id_is_rejected_by_argparse(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _run_main(["purge-duplicates", "abc"])

        assert exc_info.value.code == 2

    def test_empty_directory(self, capsys) -> None:
        mock_db = MagicMock()

        with (
            patch.object(purge_script, "SessionLocal", return_value=mock_db),
            patch.object(purge_script, "list_deleted_files_in_dir", return_value=[]),
            patch("builtins.input") as mock_input,
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["purge-duplicates", "7"])

        assert exc_info.value.code == 0
        mock_input.assert_not_called()
        assert "No soft-deleted files in this directory." in capsys.readouterr().out
        mock_db.close.assert_called_once()

    def test_pure_duplicate_batch_purges_all_on_yes(self) -> None:
        mock_db = MagicMock()
        candidates = [
            _candidate(file_id=1, active_sibling_count=1),
            _candidate(file_id=2, active_sibling_count=1),
        ]
        result = PurgeResult(
            file_id=0, store_item_deleted=False, s3_keys_deleted=[], s3_errors=[]
        )

        with (
            patch.object(purge_script, "SessionLocal", return_value=mock_db),
            patch.object(
                purge_script, "list_deleted_files_in_dir", return_value=candidates
            ),
            patch.object(purge_script, "is_still_duplicate", return_value=True),
            patch.object(purge_script, "get_storage", return_value=MagicMock()),
            patch.object(
                purge_script, "purge_file", return_value=result
            ) as mock_purge_file,
            patch("builtins.input", return_value="yes"),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["purge-duplicates", "7"])

        assert exc_info.value.code == 0
        assert mock_purge_file.call_count == 2

    def test_declining_batch_prompt_purges_nothing(self) -> None:
        mock_db = MagicMock()
        candidates = [_candidate(file_id=1, active_sibling_count=1)]

        with (
            patch.object(purge_script, "SessionLocal", return_value=mock_db),
            patch.object(
                purge_script, "list_deleted_files_in_dir", return_value=candidates
            ),
            patch.object(purge_script, "get_storage", return_value=MagicMock()),
            patch.object(purge_script, "purge_file") as mock_purge_file,
            patch("builtins.input", return_value="no"),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["purge-duplicates", "7"])

        assert exc_info.value.code == 0
        mock_purge_file.assert_not_called()

    def test_live_recheck_skips_no_longer_duplicate_file(self, capsys) -> None:
        mock_db = MagicMock()
        candidates = [_candidate(file_id=1, active_sibling_count=1)]

        with (
            patch.object(purge_script, "SessionLocal", return_value=mock_db),
            patch.object(
                purge_script, "list_deleted_files_in_dir", return_value=candidates
            ),
            patch.object(purge_script, "is_still_duplicate", return_value=False),
            patch.object(purge_script, "get_storage", return_value=MagicMock()),
            patch.object(purge_script, "purge_file") as mock_purge_file,
            patch("builtins.input", return_value="yes"),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["purge-duplicates", "7"])

        assert exc_info.value.code == 0
        mock_purge_file.assert_not_called()
        assert "skipped — no longer a safe duplicate" in capsys.readouterr().err

    def test_unexpected_s3_delete_after_live_recheck_is_flagged_as_error(
        self, capsys
    ) -> None:
        mock_db = MagicMock()
        candidates = [_candidate(file_id=1, active_sibling_count=1)]
        result = PurgeResult(
            file_id=1, store_item_deleted=True, s3_keys_deleted=["k"], s3_errors=[]
        )

        with (
            patch.object(purge_script, "SessionLocal", return_value=mock_db),
            patch.object(
                purge_script, "list_deleted_files_in_dir", return_value=candidates
            ),
            patch.object(purge_script, "is_still_duplicate", return_value=True),
            patch.object(purge_script, "get_storage", return_value=MagicMock()),
            patch.object(purge_script, "purge_file", return_value=result),
            patch("builtins.input", return_value="yes"),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["purge-duplicates", "7"])

        assert exc_info.value.code == 1
        assert "unexpectedly" in capsys.readouterr().err

    def test_mixed_directory_batches_duplicates_then_walks_through_remainder(
        self,
    ) -> None:
        mock_db = MagicMock()
        duplicate = _candidate(file_id=1, active_sibling_count=1)
        shared = _candidate(file_id=2, other_deleted_sibling_count=1)
        sole = _candidate(file_id=3)
        result = PurgeResult(
            file_id=0, store_item_deleted=False, s3_keys_deleted=[], s3_errors=[]
        )

        with (
            patch.object(purge_script, "SessionLocal", return_value=mock_db),
            patch.object(
                purge_script,
                "list_deleted_files_in_dir",
                return_value=[duplicate, shared, sole],
            ),
            patch.object(purge_script, "is_still_duplicate", return_value=True),
            patch.object(
                purge_script, "refresh_candidate", side_effect=lambda _db, c: c
            ),
            patch.object(purge_script, "get_storage", return_value=MagicMock()),
            patch.object(
                purge_script, "purge_file", return_value=result
            ) as mock_purge_file,
            # batch confirm "yes", then shared="no" (declined), sole="yes"
            patch("builtins.input", side_effect=["yes", "no", "yes"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["purge-duplicates", "7"])

        assert exc_info.value.code == 0
        purged_ids = [call.args[2] for call in mock_purge_file.call_args_list]
        assert purged_ids == [1, 3]

    def test_walkthrough_decline_does_not_abort_remaining_files(self) -> None:
        mock_db = MagicMock()
        sole_a = _candidate(file_id=1)
        sole_b = _candidate(file_id=2)
        result = PurgeResult(
            file_id=0, store_item_deleted=True, s3_keys_deleted=["k"], s3_errors=[]
        )

        with (
            patch.object(purge_script, "SessionLocal", return_value=mock_db),
            patch.object(
                purge_script,
                "list_deleted_files_in_dir",
                return_value=[sole_a, sole_b],
            ),
            patch.object(
                purge_script, "refresh_candidate", side_effect=lambda _db, c: c
            ),
            patch.object(purge_script, "get_storage", return_value=MagicMock()),
            patch.object(
                purge_script, "purge_file", return_value=result
            ) as mock_purge_file,
            patch("builtins.input", side_effect=["no", "yes"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["purge-duplicates", "7"])

        assert exc_info.value.code == 0
        mock_purge_file.assert_called_once()
        assert mock_purge_file.call_args.args[2] == 2

    def test_walkthrough_purge_error_sets_exit_code_one(self, capsys) -> None:
        mock_db = MagicMock()
        sole = _candidate(file_id=1)
        result = PurgeResult(
            file_id=1, store_item_deleted=True, s3_keys_deleted=[], s3_errors=["boom"]
        )

        with (
            patch.object(purge_script, "SessionLocal", return_value=mock_db),
            patch.object(
                purge_script, "list_deleted_files_in_dir", return_value=[sole]
            ),
            patch.object(
                purge_script, "refresh_candidate", side_effect=lambda _db, c: c
            ),
            patch.object(purge_script, "get_storage", return_value=MagicMock()),
            patch.object(purge_script, "purge_file", return_value=result),
            patch("builtins.input", return_value="yes"),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["purge-duplicates", "7"])

        assert exc_info.value.code == 1
        assert "boom" in capsys.readouterr().err

    def test_walkthrough_recomputes_impact_before_each_prompt(self, capsys) -> None:
        """Regression test: the note/prompt shown must reflect the freshly
        recomputed impact, not the stale snapshot from the initial listing —
        otherwise a file that became SOLE because an earlier sibling was
        already purged in this same walkthrough would still show the old
        SHARED note."""
        mock_db = MagicMock()
        stale_shared = _candidate(file_id=1, other_deleted_sibling_count=1)
        fresh_sole = _candidate(file_id=1, other_deleted_sibling_count=0)

        with (
            patch.object(purge_script, "SessionLocal", return_value=mock_db),
            patch.object(
                purge_script,
                "list_deleted_files_in_dir",
                return_value=[stale_shared],
            ),
            patch.object(
                purge_script, "refresh_candidate", return_value=fresh_sole
            ) as mock_refresh,
            patch.object(purge_script, "get_storage", return_value=MagicMock()),
            patch.object(purge_script, "purge_file") as mock_purge_file,
            patch("builtins.input", return_value="no"),
            pytest.raises(SystemExit),
        ):
            _run_main(["purge-duplicates", "7"])

        mock_refresh.assert_called_once_with(mock_db, stale_shared)
        mock_purge_file.assert_not_called()
        out = capsys.readouterr().out
        assert "WARNING: this is the ONLY reference" in out
        assert "other soft-deleted file(s)" not in out
