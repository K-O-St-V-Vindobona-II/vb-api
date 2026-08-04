"""Regression tests for scripts/maintain_deleted_archive_files.py.

purge_file()/restore_file()/list_deleted_files()/active_duplicates_*()
themselves are already covered by tests/test_archive_maintenance_service.py
— these tests only exercise the CLI wrapper's own logic (subcommand
parsing, confirmation prompts, exit codes, output formatting).
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

import scripts.maintain_deleted_archive_files as maintain_script
from app.services.archive_maintenance_service import (
    ActiveDuplicate,
    ArchiveMaintenanceError,
    DirLocation,
    FileLocation,
    PurgeCandidate,
    PurgeResult,
)
from tests.scripts._subprocess_helpers import (
    assert_module_imports_and_configures_mappers,
)


def _run_main(argv: list[str]) -> None:
    with patch("sys.argv", ["maintain_deleted_archive_files.py", *argv]):
        maintain_script.main()


def _candidate(
    file_id: int = 1,
    path: str = "Fotos",
    filename: str = "gruppenfoto.jpg",
    archive_dir_id: int = 7,
    active_sibling_count: int = 0,
    other_deleted_sibling_count: int = 0,
) -> PurgeCandidate:
    name, _, extension = filename.rpartition(".")
    return PurgeCandidate(
        file_id=file_id,
        path=path,
        name=name,
        extension=extension,
        description="Gruppenfoto",
        deleted_at=datetime(2026, 7, 20, 14, 32, 10, tzinfo=UTC),
        size=100,
        sha256_hash="a" * 64,
        archive_dir_id=archive_dir_id,
        archive_store_item_id=file_id,
        active_sibling_count=active_sibling_count,
        other_deleted_sibling_count=other_deleted_sibling_count,
    )


def test_standalone_import_configures_mappers_without_error() -> None:
    assert_module_imports_and_configures_mappers(
        "scripts.maintain_deleted_archive_files"
    )


def test_no_command_prints_help_without_touching_db(capsys) -> None:
    with (
        patch.object(maintain_script, "SessionLocal") as mock_session_local,
        patch.object(maintain_script, "list_deleted_files") as mock_list_deleted_files,
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
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(
            maintain_script, "list_deleted_files", return_value=[_candidate()]
        ),
        patch.object(maintain_script, "purge_file") as mock_purge_file,
        patch("builtins.input") as mock_input,
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["list"])

    assert exc_info.value.code == 0
    mock_input.assert_not_called()
    mock_purge_file.assert_not_called()
    out = capsys.readouterr().out
    assert "Fotos" in out
    assert "gruppenfoto" in out
    assert "jpg" in out
    assert "2026-07-20" in out
    assert "(dir_id: 7)" in out
    assert "CREATED_BY" not in out
    mock_db.close.assert_called_once()


def test_list_with_no_candidates(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(maintain_script, "list_deleted_files", return_value=[]),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["list"])

    assert exc_info.value.code == 0
    assert "No soft-deleted archive files found." in capsys.readouterr().out


def test_list_groups_by_directory_sorted_alphabetically_with_repeated_header(
    capsys,
) -> None:
    mock_db = MagicMock()
    candidates = [
        _candidate(file_id=1, path="Zebra", archive_dir_id=20, filename="zfile.jpg"),
        _candidate(file_id=2, path="Antilope", archive_dir_id=10, filename="afile.jpg"),
        _candidate(file_id=3, path="Antilope", archive_dir_id=10, filename="bfile.jpg"),
    ]

    with (
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(maintain_script, "list_deleted_files", return_value=candidates),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["list"])

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert out.count("ID") >= 2  # header line repeated per directory paragraph
    antilope_pos = out.index("Antilope (dir_id: 10)")
    zebra_pos = out.index("Zebra (dir_id: 20)")
    assert antilope_pos < zebra_pos  # alphabetical, not insertion order
    assert out.index("afile") < out.index("bfile") < zebra_pos  # both under Antilope
    assert out.index("zfile") > zebra_pos  # under Zebra


def test_list_with_dir_id_filters_to_one_directory(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(
            maintain_script, "list_deleted_files_in_dir", return_value=[_candidate()]
        ) as mock_list_in_dir,
        patch.object(maintain_script, "list_deleted_files") as mock_list_all,
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["list", "7"])

    assert exc_info.value.code == 0
    mock_list_in_dir.assert_called_once_with(mock_db, 7)
    mock_list_all.assert_not_called()
    assert "(dir_id: 7)" in capsys.readouterr().out


def test_list_with_non_integer_dir_id_is_rejected_by_argparse() -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_main(["list", "abc"])

    assert exc_info.value.code == 2


def test_list_short_shows_one_line_per_directory_with_count(capsys) -> None:
    mock_db = MagicMock()
    candidates = [
        _candidate(file_id=1, path="Zebra", archive_dir_id=20, filename="z.jpg"),
        _candidate(file_id=2, path="Antilope", archive_dir_id=10, filename="a.jpg"),
        _candidate(file_id=3, path="Antilope", archive_dir_id=10, filename="b.jpg"),
    ]

    with (
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(maintain_script, "list_deleted_files", return_value=candidates),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["list", "--short"])

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if "(dir_id:" in line]
    assert lines == ["Antilope (dir_id: 10): 2", "Zebra (dir_id: 20): 1"]
    # no per-file detail in short mode
    assert "z.jpg" not in out
    assert "a.jpg" not in out
    assert "ID" not in out
    # aggregate footer is still shown
    assert "3 soft-deleted file(s)" in out


def test_list_short_combines_with_dir_id(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(
            maintain_script,
            "list_deleted_files_in_dir",
            return_value=[_candidate(archive_dir_id=7)],
        ) as mock_list_in_dir,
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_main(["list", "7", "--short"])

    assert exc_info.value.code == 0
    mock_list_in_dir.assert_called_once_with(mock_db, 7)
    assert "Fotos (dir_id: 7): 1" in capsys.readouterr().out


def test_list_truncates_long_filename_and_keeps_columns_aligned(capsys) -> None:
    mock_db = MagicMock()
    long_name = "a" * 50 + ".pdf"
    candidates = [_candidate(file_id=1, filename=long_name)]

    with (
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(maintain_script, "list_deleted_files", return_value=candidates),
        pytest.raises(SystemExit),
    ):
        _run_main(["list"])

    out = capsys.readouterr().out
    assert "…" in out
    assert "a" * 50 not in out
    # SIZE column (right after FILENAME/FILEEXT) must still show up intact —
    # proves the long name didn't push the following columns out of alignment.
    assert "100.0B" in out


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
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(
            maintain_script, "list_deleted_files", return_value=[_candidate(1)]
        ),
        patch.object(maintain_script, "purge_file") as mock_purge_file,
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
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(
            maintain_script, "list_deleted_files", return_value=[_candidate(1)]
        ),
        patch.object(maintain_script, "purge_file") as mock_purge_file,
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
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(maintain_script, "list_deleted_files", return_value=candidates),
        patch.object(maintain_script, "get_storage", return_value=MagicMock()),
        patch.object(
            maintain_script, "purge_file", return_value=result
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
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(
            maintain_script, "list_deleted_files", return_value=[_candidate(1)]
        ),
        patch.object(maintain_script, "get_storage", return_value=MagicMock()),
        patch.object(
            maintain_script, "purge_file", side_effect=ArchiveMaintenanceError("boom")
        ),
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
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(
            maintain_script, "list_deleted_files", return_value=[_candidate(1)]
        ),
        patch.object(maintain_script, "get_storage", return_value=MagicMock()),
        patch.object(maintain_script, "purge_file", return_value=result),
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
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(
            maintain_script, "list_deleted_files", return_value=[_candidate(1)]
        ),
        patch.object(maintain_script, "get_storage", return_value=MagicMock()),
        patch.object(maintain_script, "purge_file", return_value=result),
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
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(maintain_script, "list_deleted_files", return_value=candidates),
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
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(
            maintain_script,
            "list_deleted_files",
            return_value=[_candidate(1, active_sibling_count=2)],
        ),
        patch.object(maintain_script, "purge_file") as mock_purge_file,
        patch("builtins.input", return_value="no") as mock_input,
        pytest.raises(SystemExit),
    ):
        _run_main(["purge", "1"])

    out = capsys.readouterr().out
    assert "2 active file(s)" in out
    assert "will NOT be deleted" in out
    prompt = mock_input.call_args[0][0]
    assert "DB only" in prompt
    mock_purge_file.assert_not_called()


def test_purge_confirm_shows_shared_note(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(
            maintain_script,
            "list_deleted_files",
            return_value=[_candidate(1, other_deleted_sibling_count=3)],
        ),
        patch("builtins.input", return_value="no") as mock_input,
        pytest.raises(SystemExit),
    ):
        _run_main(["purge", "1"])

    out = capsys.readouterr().out
    assert "3 other" in out
    assert "removed once the last referencing file is purged" in out
    prompt = mock_input.call_args[0][0]
    assert "DB only" in prompt


def test_purge_confirm_shows_sole_warning(capsys) -> None:
    mock_db = MagicMock()

    with (
        patch.object(maintain_script, "SessionLocal", return_value=mock_db),
        patch.object(
            maintain_script, "list_deleted_files", return_value=[_candidate(1)]
        ),
        patch("builtins.input", return_value="no") as mock_input,
        pytest.raises(SystemExit),
    ):
        _run_main(["purge", "1"])

    out = capsys.readouterr().out
    assert "WARNING: this is the ONLY reference" in out
    prompt = mock_input.call_args[0][0]
    assert "DB AND S3" in prompt


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
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(maintain_script, "list_deleted_files_in_dir", return_value=[]),
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
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script, "list_deleted_files_in_dir", return_value=candidates
            ),
            patch.object(maintain_script, "is_still_duplicate", return_value=True),
            patch.object(maintain_script, "get_storage", return_value=MagicMock()),
            patch.object(
                maintain_script, "purge_file", return_value=result
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
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script, "list_deleted_files_in_dir", return_value=candidates
            ),
            patch.object(maintain_script, "get_storage", return_value=MagicMock()),
            patch.object(maintain_script, "purge_file") as mock_purge_file,
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
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script, "list_deleted_files_in_dir", return_value=candidates
            ),
            patch.object(maintain_script, "is_still_duplicate", return_value=False),
            patch.object(maintain_script, "get_storage", return_value=MagicMock()),
            patch.object(maintain_script, "purge_file") as mock_purge_file,
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
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script, "list_deleted_files_in_dir", return_value=candidates
            ),
            patch.object(maintain_script, "is_still_duplicate", return_value=True),
            patch.object(maintain_script, "get_storage", return_value=MagicMock()),
            patch.object(maintain_script, "purge_file", return_value=result),
            patch("builtins.input", return_value="yes"),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["purge-duplicates", "7"])

        assert exc_info.value.code == 1
        assert "unexpectedly" in capsys.readouterr().err

    def test_no_duplicates_reports_remainder_without_prompting(self, capsys) -> None:
        mock_db = MagicMock()
        shared = _candidate(file_id=2, other_deleted_sibling_count=1)
        sole = _candidate(file_id=3)

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script,
                "list_deleted_files_in_dir",
                return_value=[shared, sole],
            ),
            patch.object(maintain_script, "purge_file") as mock_purge_file,
            patch("builtins.input") as mock_input,
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["purge-duplicates", "7"])

        assert exc_info.value.code == 0
        mock_input.assert_not_called()
        mock_purge_file.assert_not_called()
        out = capsys.readouterr().out
        assert "No safely-duplicated files" in out
        assert "2 file(s) need individual review" in out

    def test_remainder_is_reported_but_not_processed(self, capsys) -> None:
        """The removed walkthrough is gone for good: purge-duplicates must
        only ever prompt once (the batch prompt) and only ever purge the
        duplicate candidates — shared/sole files are left untouched, just
        reported with a count."""
        mock_db = MagicMock()
        duplicate = _candidate(file_id=1, active_sibling_count=1)
        shared = _candidate(file_id=2, other_deleted_sibling_count=1)
        sole = _candidate(file_id=3)
        result = PurgeResult(
            file_id=0, store_item_deleted=False, s3_keys_deleted=[], s3_errors=[]
        )

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script,
                "list_deleted_files_in_dir",
                return_value=[duplicate, shared, sole],
            ),
            patch.object(maintain_script, "is_still_duplicate", return_value=True),
            patch.object(maintain_script, "get_storage", return_value=MagicMock()),
            patch.object(
                maintain_script, "purge_file", return_value=result
            ) as mock_purge_file,
            patch("builtins.input", return_value="yes") as mock_input,
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["purge-duplicates", "7"])

        assert exc_info.value.code == 0
        mock_input.assert_called_once()  # only the batch prompt, no walkthrough
        mock_purge_file.assert_called_once()
        assert mock_purge_file.call_args.args[2] == 1
        out = capsys.readouterr().out
        assert "2 remaining file(s)" in out
        assert "list-duplicates" in out


class TestRestoreCommand:
    def test_no_file_id_is_rejected_by_argparse(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _run_main(["restore"])

        assert exc_info.value.code == 2

    def test_non_integer_file_id_is_rejected_by_argparse(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _run_main(["restore", "abc"])

        assert exc_info.value.code == 2

    def test_success_without_confirmation_prompt(self, capsys) -> None:
        mock_db = MagicMock()
        location = FileLocation(
            file_id=42, path="Fotos", name="bild", extension="jpg", deleted=False
        )

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script, "restore_file", return_value=location
            ) as mock_restore_file,
            patch("builtins.input") as mock_input,
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["restore", "42"])

        assert exc_info.value.code == 0
        mock_input.assert_not_called()
        mock_restore_file.assert_called_once_with(mock_db, 42)
        out = capsys.readouterr().out
        assert "Restored file 42" in out
        assert "bild.jpg" in out
        assert "Fotos" in out
        mock_db.close.assert_called_once()

    def test_error_is_reported_exit_code_one(self, capsys) -> None:
        mock_db = MagicMock()

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script,
                "restore_file",
                side_effect=ArchiveMaintenanceError("nicht gefunden"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["restore", "999"])

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "WARNING: file 999 not restored" in err
        assert "nicht gefunden" in err

    def test_multiple_ids_processed_in_order(self) -> None:
        mock_db = MagicMock()
        loc1 = FileLocation(
            file_id=1, path="Fotos", name="a", extension="jpg", deleted=False
        )
        loc2 = FileLocation(
            file_id=2, path="Fotos", name="b", extension="jpg", deleted=False
        )

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script, "restore_file", side_effect=[loc1, loc2]
            ) as mock_restore_file,
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["restore", "1", "2"])

        assert exc_info.value.code == 0
        assert [c.args[1] for c in mock_restore_file.call_args_list] == [1, 2]

    def test_unrestorable_id_warns_and_does_not_stop_the_batch(self, capsys) -> None:
        """A file that isn't SOLE (or otherwise can't be restored) must not
        abort the remaining ids — only a warning is printed for it."""
        mock_db = MagicMock()
        loc1 = FileLocation(
            file_id=1, path="Fotos", name="a", extension="jpg", deleted=False
        )
        loc3 = FileLocation(
            file_id=3, path="Fotos", name="c", extension="jpg", deleted=False
        )

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script,
                "restore_file",
                side_effect=[
                    loc1,
                    ArchiveMaintenanceError("Archivdatei 2 ist nicht SOLE"),
                    loc3,
                ],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["restore", "1", "2", "3"])

        assert exc_info.value.code == 1
        out, err = capsys.readouterr()
        assert "Restored file 1" in out
        assert "Restored file 3" in out
        assert "WARNING: file 2 not restored" in err
        assert "nicht SOLE" in err
        assert "2 file(s) restored, 1 failed." in out

    def test_summary_line_omitted_for_single_id(self, capsys) -> None:
        mock_db = MagicMock()
        location = FileLocation(
            file_id=42, path="Fotos", name="bild", extension="jpg", deleted=False
        )

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(maintain_script, "restore_file", return_value=location),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["restore", "42"])

        assert exc_info.value.code == 0
        assert "file(s) restored" not in capsys.readouterr().out


class TestListDuplicatesCommand:
    def test_neither_file_nor_dir_is_rejected(self, capsys) -> None:
        mock_db = MagicMock()

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["list-duplicates"])

        assert exc_info.value.code == 2
        assert "at least one --file or --dir" in capsys.readouterr().err

    def test_single_deleted_file_with_no_duplicates(self, capsys) -> None:
        mock_db = MagicMock()
        location = FileLocation(
            file_id=1, path="Fotos", name="a", extension="jpg", deleted=True
        )

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script,
                "active_duplicates_of_file",
                return_value=(location, []),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["list-duplicates", "--file", "1"])

        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "DELETED FILE:" in out
        assert "ACTIVE FILE:" not in out
        assert "DUPLICATES:" in out
        assert "(none)" in out
        assert "1" in out
        assert "Fotos" in out
        assert "a.jpg" in out

    def test_single_active_file_shows_active_label(self, capsys) -> None:
        """--file accepts any status, not just soft-deleted ones — the
        section label must reflect the file's actual current state."""
        mock_db = MagicMock()
        location = FileLocation(
            file_id=1, path="Fotos", name="a", extension="jpg", deleted=False
        )

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script,
                "active_duplicates_of_file",
                return_value=(location, []),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["list-duplicates", "--file", "1"])

        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "ACTIVE FILE:" in out
        assert "DELETED FILE:" not in out

    def test_multiple_file_flags_processed_in_order(self) -> None:
        mock_db = MagicMock()
        loc1 = FileLocation(
            file_id=1, path="Fotos", name="a", extension="jpg", deleted=True
        )
        loc2 = FileLocation(
            file_id=2, path="Fotos", name="b", extension="jpg", deleted=True
        )

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script,
                "active_duplicates_of_file",
                side_effect=[(loc1, []), (loc2, [])],
            ) as mock_active_dup,
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["list-duplicates", "--file", "1", "--file", "2"])

        assert exc_info.value.code == 0
        assert [c.args[1] for c in mock_active_dup.call_args_list] == [1, 2]

    def test_multiple_blocks_separated_by_delimiter(self, capsys) -> None:
        mock_db = MagicMock()
        loc1 = FileLocation(
            file_id=1, path="Fotos", name="a", extension="jpg", deleted=True
        )
        loc2 = FileLocation(
            file_id=2, path="Fotos", name="b", extension="jpg", deleted=True
        )

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script,
                "active_duplicates_of_file",
                side_effect=[(loc1, []), (loc2, [])],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["list-duplicates", "--file", "1", "--file", "2"])

        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert out.count("=====") == 1  # only between the two blocks, not around them
        assert not out.strip().startswith("=====")
        assert not out.strip().endswith("=====")

    def test_file_not_found_reports_error_but_continues(self, capsys) -> None:
        mock_db = MagicMock()
        loc = FileLocation(
            file_id=2, path="Fotos", name="b", extension="jpg", deleted=True
        )

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script,
                "active_duplicates_of_file",
                side_effect=[
                    ArchiveMaintenanceError("Archivdatei 1 nicht gefunden."),
                    (loc, []),
                ],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["list-duplicates", "--file", "1", "--file", "2"])

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "nicht gefunden" in captured.err
        assert "DELETED FILE:" in captured.out
        assert "(none)" in captured.out

    def test_dir_with_no_soft_deleted_files(self, capsys) -> None:
        mock_db = MagicMock()

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(maintain_script, "active_duplicates_in_dir", return_value=[]),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["list-duplicates", "--dir", "7"])

        assert exc_info.value.code == 0
        assert "no soft-deleted files" in capsys.readouterr().out.lower()

    def test_dir_lists_pairs_with_active_duplicates(self, capsys) -> None:
        mock_db = MagicMock()
        candidate = _candidate(file_id=1)
        dup = ActiveDuplicate(file_id=99, path="Aktiv", name="x", extension="jpg")

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script,
                "active_duplicates_in_dir",
                return_value=[(candidate, [dup])],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["list-duplicates", "--dir", "7"])

        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        # files from --dir are always currently soft-deleted, by construction
        assert "DELETED FILE:" in out
        assert "ACTIVE FILE:" not in out
        assert "DUPLICATES:" in out
        assert "99" in out
        assert "Aktiv" in out
        assert "x.jpg" in out

    def test_combines_file_and_dir_in_one_call(self) -> None:
        mock_db = MagicMock()
        loc = FileLocation(
            file_id=1, path="Fotos", name="a", extension="jpg", deleted=True
        )

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script,
                "active_duplicates_of_file",
                return_value=(loc, []),
            ) as mock_file,
            patch.object(
                maintain_script, "active_duplicates_in_dir", return_value=[]
            ) as mock_dir,
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["list-duplicates", "--file", "1", "--dir", "10"])

        assert exc_info.value.code == 0
        mock_file.assert_called_once_with(mock_db, 1)
        mock_dir.assert_called_once_with(mock_db, 10)


class TestUrlpathCommand:
    def test_no_id_is_rejected_by_argparse(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _run_main(["urlpath"])

        assert exc_info.value.code == 2

    def test_non_integer_id_is_rejected_by_argparse(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _run_main(["urlpath", "abc"])

        assert exc_info.value.code == 2

    def test_neither_file_nor_dir_found(self, capsys) -> None:
        mock_db = MagicMock()

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(maintain_script, "find_file_location", return_value=None),
            patch.object(maintain_script, "find_dir_location", return_value=None),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["urlpath", "42"])

        assert exc_info.value.code == 1
        assert "no file or directory with id 42" in capsys.readouterr().err
        mock_db.close.assert_called_once()

    def test_deleted_file_only(self, capsys) -> None:
        mock_db = MagicMock()
        location = FileLocation(
            file_id=42, path="Fotos", name="bild", extension="jpg", deleted=True
        )

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(maintain_script, "find_file_location", return_value=location),
            patch.object(maintain_script, "find_dir_location", return_value=None),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["urlpath", "42"])

        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "DELETED FILE:" in out
        assert "ACTIVE FILE:" not in out
        assert "42" in out
        assert "Fotos" in out
        assert "bild.jpg" in out
        assert "URL PATH: /archive/files/42" in out
        assert "DIRECTORY" not in out

    def test_active_file_shows_active_label(self, capsys) -> None:
        mock_db = MagicMock()
        location = FileLocation(
            file_id=42, path="Fotos", name="bild", extension="jpg", deleted=False
        )

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(maintain_script, "find_file_location", return_value=location),
            patch.object(maintain_script, "find_dir_location", return_value=None),
            pytest.raises(SystemExit),
        ):
            _run_main(["urlpath", "42"])

        out = capsys.readouterr().out
        assert "ACTIVE FILE:" in out
        assert "DELETED FILE:" not in out

    def test_active_dir_only(self, capsys) -> None:
        mock_db = MagicMock()
        location = DirLocation(dir_id=7, path="Bilder / 2012", deleted=False)

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(maintain_script, "find_file_location", return_value=None),
            patch.object(maintain_script, "find_dir_location", return_value=location),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["urlpath", "7"])

        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "ACTIVE DIRECTORY:" in out
        assert "DELETED DIRECTORY:" not in out
        assert "Bilder / 2012" in out
        assert "URL PATH: /archive/dirs/7" in out
        assert "FILE:" not in out

    def test_deleted_dir_shows_deleted_label(self, capsys) -> None:
        mock_db = MagicMock()
        location = DirLocation(dir_id=7, path="Bilder / 2012", deleted=True)

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(maintain_script, "find_file_location", return_value=None),
            patch.object(maintain_script, "find_dir_location", return_value=location),
            pytest.raises(SystemExit),
        ):
            _run_main(["urlpath", "7"])

        assert "DELETED DIRECTORY:" in capsys.readouterr().out

    def test_both_file_and_dir_found_shows_both_with_delimiter(self, capsys) -> None:
        mock_db = MagicMock()
        file_location = FileLocation(
            file_id=7, path="Fotos", name="bild", extension="jpg", deleted=True
        )
        dir_location = DirLocation(dir_id=7, path="Bilder / 2012", deleted=False)

        with (
            patch.object(maintain_script, "SessionLocal", return_value=mock_db),
            patch.object(
                maintain_script, "find_file_location", return_value=file_location
            ),
            patch.object(
                maintain_script, "find_dir_location", return_value=dir_location
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["urlpath", "7"])

        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "DELETED FILE:" in out
        assert "ACTIVE DIRECTORY:" in out
        assert "URL PATH: /archive/files/7" in out
        assert "URL PATH: /archive/dirs/7" in out
        assert out.count("=====") == 1
