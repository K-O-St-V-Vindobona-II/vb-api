"""Tests for app/core/worker_logging.py.

app.worker already calls install_task_origin_log_filter() once at module
import time (test_worker.py imports app.worker, so the filter is already
attached to the 'arq.worker' logger by the time these tests run) — these
tests exercise the filter directly against synthetic LogRecords rather
than driving a real arq Worker, mirroring how test_worker.py unit-tests
this module's own wiring without a live arq/Valkey connection.
"""

import logging

from app.core.worker_logging import (
    TaskOriginLogFilter,
    describe_job_origin,
    install_task_origin_log_filter,
)


def _make_record(msg: str, args: tuple[object, ...]) -> logging.LogRecord:
    return logging.LogRecord(
        name="arq.worker",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


class TestTaskOriginLogFilter:
    def test_tags_cron_job_start_line_as_scheduled(self) -> None:
        # Cron jobs get a bare function-name ref (no colon) -- see
        # arq.worker.Worker.run_job()'s `ref = function_name` branch.
        record = _make_record("%6.2fs → %s(%s)%s", (1.0, "task_cleanup", "", ""))

        TaskOriginLogFilter().filter(record)

        assert record.msg == "[scheduled] %6.2fs → %s(%s)%s"

    def test_tags_ad_hoc_job_end_line_as_triggered(self) -> None:
        # Ad-hoc enqueue_job() runs get a '<job_id>:<function_name>' ref
        # -- see the `ref = f'{job_id}:{function_name}'` branch.
        ref = "6fddae81290645d3931536be32b01740:task_send_reset_email"
        record = _make_record("%6.2fs ← %s ● %s", (1.67, ref, ""))

        TaskOriginLogFilter().filter(record)

        assert record.msg == "[triggered] %6.2fs ← %s ● %s"

    def test_leaves_startup_banner_untouched(self) -> None:
        # Regression guard: this line also happens to carry 2 args where
        # the second is a plain string, but it is not a job-lifecycle
        # line and must never get tagged.
        record = _make_record(
            "Starting worker for %d functions: %s",
            (14, "task_cleanup, task_health_check"),
        )

        TaskOriginLogFilter().filter(record)

        assert record.msg == "Starting worker for %d functions: %s"

    def test_leaves_health_recording_line_untouched(self) -> None:
        # Only 1 arg -- shorter than the 2-arg shape every job-lifecycle
        # template carries.
        record = _make_record("recording health: %s", ("j_complete=1",))

        TaskOriginLogFilter().filter(record)

        assert record.msg == "recording health: %s"

    def test_filter_always_returns_true(self) -> None:
        # A Filter returning False would drop the record entirely --
        # this filter only ever tags, never suppresses.
        record = _make_record("%6.2fs → %s(%s)%s", (1.0, "task_cleanup", "", ""))

        assert TaskOriginLogFilter().filter(record) is True


class TestDescribeJobOrigin:
    def test_cron_generated_job_id_is_scheduled(self) -> None:
        # arq.worker.Worker.run_cron() builds cron job_ids as
        # '<name>:<next run in unix ms>' for every entry in this
        # codebase (all use unique=True).
        assert describe_job_origin("task_downsync:1735689600000") == "scheduled"

    def test_random_hex_job_id_is_triggered(self) -> None:
        # Default shape of a job_id arq assigns when enqueue_job() is
        # called without an explicit _job_id -- every ad-hoc trigger in
        # this codebase.
        assert describe_job_origin("5f46437f49894025acc145be5d22df28") == "triggered"


class TestInstallTaskOriginLogFilter:
    def test_installing_twice_attaches_exactly_one_filter(self) -> None:
        install_task_origin_log_filter()
        install_task_origin_log_filter()

        target = logging.getLogger("arq.worker")
        matching = [f for f in target.filters if isinstance(f, TaskOriginLogFilter)]
        assert len(matching) == 1
