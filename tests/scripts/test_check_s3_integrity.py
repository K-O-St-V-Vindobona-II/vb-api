"""Regression tests for scripts/check_s3_integrity.py.

The completeness/orphan-detection logic itself is covered by
tests/test_storage_integrity_service.py — these tests only exercise the
CLI wrapper's own logic (formatting, control flow, exit codes).
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.exc import OperationalError

import scripts.check_s3_integrity as check_s3_integrity
from app.services.storage_integrity_service import IntegrityReport
from tests.scripts._subprocess_helpers import (
    assert_module_imports_and_configures_mappers,
)


def test_standalone_import_configures_mappers_without_error() -> None:
    """Run as a fresh process (not sharing pytest's conftest-populated
    SQLAlchemy registry) — a plain in-process import can't detect a
    missing `import app.db.base`, since conftest.py already registers
    every model for the whole test session before this test body even
    runs."""
    assert_module_imports_and_configures_mappers("scripts.check_s3_integrity")


def test_human_size_formatting() -> None:
    assert check_s3_integrity._human_size(500) == "500.0B"
    assert check_s3_integrity._human_size(2048) == "2.0KB"
    assert check_s3_integrity._human_size(5 * 1024 * 1024) == "5.0MB"


def test_print_report_no_issues(capsys, mock_s3) -> None:
    report = IntegrityReport(missing=[], orphans=[])
    check_s3_integrity.print_report("Archive", mock_s3, report)
    out = capsys.readouterr().out
    assert "0 missing file(s)." in out
    assert "No orphaned files found." in out


def test_print_report_shows_missing(capsys, mock_s3) -> None:
    report = IntegrityReport(missing=["archive/store/abc"], orphans=[])
    check_s3_integrity.print_report("Archive", mock_s3, report)
    out = capsys.readouterr().out
    assert "MISSING: archive/store/abc" in out


def test_print_report_shows_orphan_details(capsys, mock_s3) -> None:
    mock_s3.upload("archive/store/orphan_hash", b"some content", "application/pdf")
    report = IntegrityReport(missing=[], orphans=["archive/store/orphan_hash"])

    check_s3_integrity.print_report("Archive", mock_s3, report)

    out = capsys.readouterr().out
    assert "orphan_hash" in out
    assert "application/pdf" in out
    assert "information-only report" in out


def test_main_exits_zero_when_all_healthy(capsys) -> None:
    healthy = IntegrityReport(missing=[], orphans=[])
    with (
        patch.object(check_s3_integrity, "get_storage", return_value=MagicMock()),
        patch.object(check_s3_integrity, "SessionLocal", return_value=MagicMock()),
        patch.object(
            check_s3_integrity, "check_archive_integrity", return_value=healthy
        ),
        patch.object(
            check_s3_integrity, "check_standesdb_integrity", return_value=healthy
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        check_s3_integrity.main()

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "=== Archive ===" in out
    assert "=== Standesdb ===" in out


def test_main_exits_one_when_files_missing() -> None:
    bad = IntegrityReport(missing=["archive/store/x"], orphans=[])
    healthy = IntegrityReport(missing=[], orphans=[])
    with (
        patch.object(check_s3_integrity, "get_storage", return_value=MagicMock()),
        patch.object(check_s3_integrity, "SessionLocal", return_value=MagicMock()),
        patch.object(check_s3_integrity, "check_archive_integrity", return_value=bad),
        patch.object(
            check_s3_integrity, "check_standesdb_integrity", return_value=healthy
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        check_s3_integrity.main()

    assert exc_info.value.code == 1


@pytest.mark.parametrize(
    ("last_modified", "expected"),
    [
        (datetime(2026, 7, 14, 22, 30, tzinfo=UTC), "2026-07-15 00:30 CEST"),
        (datetime(2026, 1, 15, 22, 30, tzinfo=UTC), "2026-01-15 23:30 CET"),
    ],
)
def test_last_modified_is_shown_in_the_application_time_zone(
    capsys, last_modified, expected
) -> None:
    storage = MagicMock()
    storage.head.return_value = {
        "size": 10,
        "content_type": "image/png",
        "last_modified": last_modified,
    }

    check_s3_integrity.print_report(
        "Archive", storage, IntegrityReport(missing=[], orphans=["k"])
    )

    assert expected in capsys.readouterr().out


def _client_error() -> ClientError:
    return ClientError({"Error": {"Code": "404", "Message": "gone"}}, "HeadObject")


def _run_main(
    *,
    archive: IntegrityReport | Exception,
    standesdb: IntegrityReport | Exception | None = None,
    db: MagicMock | None = None,
) -> object:
    """Runs main() against stand-ins and returns the exit code."""
    standesdb = standesdb or IntegrityReport(missing=[], orphans=[])
    with (
        patch.object(check_s3_integrity, "get_storage", return_value=MagicMock()),
        patch.object(
            check_s3_integrity, "SessionLocal", return_value=db or MagicMock()
        ),
        patch.object(
            check_s3_integrity, "check_archive_integrity", side_effect=[archive]
        ),
        patch.object(
            check_s3_integrity, "check_standesdb_integrity", side_effect=[standesdb]
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        check_s3_integrity.main()
    return exc_info.value.code


class TestExitCodes:
    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            (_client_error(), "gone"),
            (BotoCoreError(), "unspecified error"),
            (OperationalError("select 1", {}, Exception("down")), "down"),
        ],
        ids=["storage-client-error", "storage-connection-error", "database-error"],
    )
    def test_a_broken_run_exits_two_not_one(self, capsys, error, reason) -> None:
        assert _run_main(archive=error) == 2

        err = capsys.readouterr().err
        assert "could not finish" in err
        assert reason in err

    def test_a_failure_in_the_second_check_also_exits_two(self, capsys) -> None:
        code = _run_main(
            archive=IntegrityReport(missing=["k"], orphans=[]),
            standesdb=_client_error(),
        )

        assert code == 2
        assert "could not finish" in capsys.readouterr().err

    def test_the_database_session_is_closed_after_a_failure(self) -> None:
        db = MagicMock()

        _run_main(archive=_client_error(), db=db)

        db.close.assert_called_once()

    def test_a_programming_error_is_not_hidden_as_a_broken_run(self) -> None:
        with (
            patch.object(check_s3_integrity, "get_storage", return_value=MagicMock()),
            patch.object(check_s3_integrity, "SessionLocal", return_value=MagicMock()),
            patch.object(
                check_s3_integrity,
                "check_archive_integrity",
                side_effect=ValueError("bug"),
            ),
            pytest.raises(ValueError, match="bug"),
        ):
            check_s3_integrity.main()

    def test_a_missing_file_still_exits_one_and_prints_both_reports(
        self, capsys
    ) -> None:
        code = _run_main(archive=IntegrityReport(missing=["k"], orphans=[]))

        assert code == 1
        out = capsys.readouterr().out
        assert "=== Archive ===" in out
        assert "=== Standesdb ===" in out


class TestVanishedOrphans:
    def test_one_vanished_object_does_not_stop_the_listing(self, capsys) -> None:
        storage = MagicMock()
        storage.head.side_effect = [
            _client_error(),
            {
                "size": 10,
                "content_type": "image/png",
                "last_modified": datetime(2026, 7, 14, 22, 30, tzinfo=UTC),
            },
        ]

        check_s3_integrity.print_report(
            "Archive", storage, IntegrityReport(missing=[], orphans=["gone", "kept"])
        )

        out = capsys.readouterr().out
        assert "gone" in out
        assert "removed or unreadable since it was listed" in out
        assert "kept" in out
        assert "10.0B" in out
        assert "information-only report" in out

    def test_the_standesdb_report_is_still_printed(self, capsys) -> None:
        storage = MagicMock()
        storage.head.side_effect = _client_error()

        with (
            patch.object(check_s3_integrity, "get_storage", return_value=storage),
            patch.object(check_s3_integrity, "SessionLocal", return_value=MagicMock()),
            patch.object(
                check_s3_integrity,
                "check_archive_integrity",
                return_value=IntegrityReport(missing=[], orphans=["a", "b"]),
            ),
            patch.object(
                check_s3_integrity,
                "check_standesdb_integrity",
                return_value=IntegrityReport(missing=[], orphans=[]),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            check_s3_integrity.main()

        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert out.count("removed or unreadable") == 2
        assert "=== Standesdb ===" in out
