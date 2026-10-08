"""Regression tests for scripts/check_s3_integrity.py.

The completeness/orphan-detection logic itself is covered by
tests/test_storage_integrity_service.py — these tests only exercise the
CLI wrapper's own logic (formatting, control flow, exit codes).
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

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
