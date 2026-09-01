"""Regression tests for app/worker.py's ARQ WorkerSettings.

Job body business logic stays covered by tests/test_scheduled_jobs.py
(the bodies themselves live in app/core/scheduler.py, unchanged) — this
file covers only this module's own wiring: the health-check task, the
per-stage cron_jobs construction, and the timezone/Redis configuration.
"""

import asyncio
from unittest.mock import Mock

from app.core.config import get_settings
from app.core.datetime_utils import get_app_timezone
from app.worker import (
    WorkerSettings,
    build_cron_jobs,
    task_archive_health_check,
    task_birthday_mails,
    task_cleanup,
    task_db_backup,
    task_debtor_reminder,
    task_downsync,
    task_health_check,
    task_refresh_category_filter_hits,
    task_send_entry_changed_email,
    task_send_member_change_request_resolved_email,
    task_send_member_change_request_submitted_email,
    task_send_own_image_changed_email,
    task_send_reset_email,
    task_standesdb_chronicles,
    task_standesdb_health_check,
)


class TestWorkerSettings:
    def test_registers_at_least_one_function(self) -> None:
        # arq refuses to start a Worker with neither functions nor
        # cron_jobs registered (RuntimeError at Worker.__init__) — this
        # regression-guards against ever landing back at an empty list.
        assert len(WorkerSettings.functions) > 0

    def test_redis_settings_is_configured(self) -> None:
        assert WorkerSettings.redis_settings is not None

    def test_uses_configured_app_timezone(self) -> None:
        # Regression guard: without an explicit timezone, arq falls back
        # to the container's local zone (UTC, since no TZ env var is
        # set), causing every human-facing cron job to fire 1-2h too
        # late. WorkerSettings.timezone is read from Settings.app_timezone
        # once at import time, not re-read dynamically per job — mirrors
        # the pre-existing APScheduler-era guarantee.
        assert WorkerSettings.timezone == get_app_timezone()


class TestTaskHealthCheck:
    def test_returns_ok(self) -> None:
        # No pytest-asyncio/anyio-pytest plugin is installed in this
        # project (the app is sync end-to-end, see app/db/database.py) —
        # asyncio.run() exercises the coroutine without adding one just
        # for this single trivial task.
        assert asyncio.run(task_health_check({})) == "ok"


class TestBuildCronJobs:
    def test_production_registers_eight_jobs_not_downsync(self, monkeypatch):
        monkeypatch.setenv("APP_ENVIRONMENT", "production")
        get_settings.cache_clear()

        jobs = build_cron_jobs()

        names = {j.name for j in jobs}
        assert "task_downsync" not in names
        assert "task_cleanup" in names
        assert "task_db_backup" in names
        assert len(jobs) == 8

    def test_non_production_registers_only_cleanup_and_downsync(self):
        # Default test env (APP_ENVIRONMENT=test, see conftest.py) is
        # already non-production — no monkeypatch needed here.
        jobs = build_cron_jobs()

        names = {j.name for j in jobs}
        assert names == {"task_cleanup", "task_downsync"}

    def test_backup_disabled_removes_db_backup_job(self, monkeypatch):
        monkeypatch.setenv("APP_ENVIRONMENT", "production")
        monkeypatch.setenv("BACKUP_ENABLED", "false")
        get_settings.cache_clear()

        jobs = build_cron_jobs()

        names = {j.name for j in jobs}
        assert "task_db_backup" not in names


class TestThinTaskWrappers:
    """Each task_* wrapper is a one-line asyncio.to_thread(job_x) or
    asyncio.to_thread(send_x, ...) delegation - untested, a typo in the
    delegated function name or a dropped argument would only surface at
    runtime, once arq actually dispatches that job."""

    def test_task_cleanup_delegates_to_job_cleanup(self, monkeypatch) -> None:
        mock_job = Mock()
        monkeypatch.setattr("app.worker.job_cleanup", mock_job)
        asyncio.run(task_cleanup({}))
        mock_job.assert_called_once_with()

    def test_task_refresh_category_filter_hits_delegates(self, monkeypatch) -> None:
        mock_job = Mock()
        monkeypatch.setattr("app.worker.job_refresh_category_filter_hits", mock_job)
        asyncio.run(task_refresh_category_filter_hits({}))
        mock_job.assert_called_once_with()

    def test_task_birthday_mails_delegates(self, monkeypatch) -> None:
        mock_job = Mock()
        monkeypatch.setattr("app.worker.job_birthday_mails", mock_job)
        asyncio.run(task_birthday_mails({}))
        mock_job.assert_called_once_with()

    def test_task_debtor_reminder_delegates(self, monkeypatch) -> None:
        mock_job = Mock()
        monkeypatch.setattr("app.worker.job_debtor_reminder", mock_job)
        asyncio.run(task_debtor_reminder({}))
        mock_job.assert_called_once_with()

    def test_task_standesdb_chronicles_delegates(self, monkeypatch) -> None:
        mock_job = Mock()
        monkeypatch.setattr("app.worker.job_standesdb_chronicles", mock_job)
        asyncio.run(task_standesdb_chronicles({}))
        mock_job.assert_called_once_with()

    def test_task_archive_health_check_delegates(self, monkeypatch) -> None:
        mock_job = Mock()
        monkeypatch.setattr("app.worker.job_archive_health_check", mock_job)
        asyncio.run(task_archive_health_check({}))
        mock_job.assert_called_once_with()

    def test_task_standesdb_health_check_delegates(self, monkeypatch) -> None:
        mock_job = Mock()
        monkeypatch.setattr("app.worker.job_standesdb_health_check", mock_job)
        asyncio.run(task_standesdb_health_check({}))
        mock_job.assert_called_once_with()

    def test_task_db_backup_delegates(self, monkeypatch) -> None:
        mock_job = Mock()
        monkeypatch.setattr("app.worker.job_db_backup", mock_job)
        asyncio.run(task_db_backup({}))
        mock_job.assert_called_once_with()

    def test_task_send_reset_email_delegates_with_args(self, monkeypatch) -> None:
        mock_send = Mock()
        monkeypatch.setattr("app.worker.send_reset_email", mock_send)
        asyncio.run(task_send_reset_email({}, "member@test.at", "tok123"))
        mock_send.assert_called_once_with("member@test.at", "tok123")

    def test_task_send_entry_changed_email_delegates_with_args(
        self, monkeypatch
    ) -> None:
        mock_send = Mock()
        monkeypatch.setattr("app.worker.send_entry_changed_email", mock_send)
        asyncio.run(
            task_send_entry_changed_email(
                {},
                ["a@test.at"],
                "member",
                "Testikus",
                {"field": {"old": "a", "new": "b"}},
                change_type="update",
                modifier_cn="Admin",
            )
        )
        mock_send.assert_called_once_with(
            ["a@test.at"],
            "member",
            "Testikus",
            {"field": {"old": "a", "new": "b"}},
            change_type="update",
            modifier_cn="Admin",
        )

    def test_task_send_member_change_request_submitted_email_delegates(
        self, monkeypatch
    ) -> None:
        mock_send = Mock()
        monkeypatch.setattr(
            "app.worker.send_member_change_request_submitted_email", mock_send
        )
        asyncio.run(
            task_send_member_change_request_submitted_email(
                {}, ["a@test.at"], "Testikus", {"field": {"old": "a", "new": "b"}}
            )
        )
        mock_send.assert_called_once_with(
            ["a@test.at"], "Testikus", {"field": {"old": "a", "new": "b"}}
        )

    def test_task_send_member_change_request_resolved_email_delegates(
        self, monkeypatch
    ) -> None:
        mock_send = Mock()
        monkeypatch.setattr(
            "app.worker.send_member_change_request_resolved_email", mock_send
        )
        asyncio.run(
            task_send_member_change_request_resolved_email(
                {},
                "a@test.at",
                {"field": {"old": "a", "new": "b"}},
                {"field": "accept"},
            )
        )
        mock_send.assert_called_once_with(
            "a@test.at", {"field": {"old": "a", "new": "b"}}, {"field": "accept"}
        )

    def test_task_send_own_image_changed_email_delegates(self, monkeypatch) -> None:
        mock_send = Mock()
        monkeypatch.setattr("app.worker.send_own_image_changed_email", mock_send)
        asyncio.run(
            task_send_own_image_changed_email({}, ["a@test.at"], "Testikus", "changed")
        )
        mock_send.assert_called_once_with(["a@test.at"], "Testikus", "changed")


class TestTaskDownsync:
    # task_downsync is registered under the same name as both a cron job
    # and an ad-hoc enqueue target (see build_cron_jobs()/its own
    # docstring) -- arq's own job-lifecycle log line can't tell those two
    # apart for this one task, so it logs its origin itself from
    # ctx["job_id"]. These regression-guard that classification directly,
    # since a flipped comparison here would silently mislabel every run.

    def test_logs_scheduled_for_a_cron_generated_job_id(self, monkeypatch) -> None:
        # Mocks app.worker's own logger reference directly rather than
        # asserting via caplog -- alembic/env.py's fileConfig() call
        # (run once per test session by the _create_schema fixture)
        # disables every logger that already existed at that point,
        # which includes every module-level logger created at test
        # collection time, app.worker's among them.
        monkeypatch.setattr("app.worker.job_downsync", lambda: None)
        mock_logger = Mock()
        monkeypatch.setattr("app.worker.logger", mock_logger)

        asyncio.run(task_downsync({"job_id": "task_downsync:1735689600000"}))

        mock_logger.info.assert_called_once_with(
            "[%s] task_downsync starting (job_id=%s)",
            "scheduled",
            "task_downsync:1735689600000",
        )

    def test_logs_triggered_for_a_random_hex_job_id(self, monkeypatch) -> None:
        # Shape of the default job_id arq assigns to enqueue_job() calls
        # that omit _job_id -- every ad-hoc trigger in this codebase.
        monkeypatch.setattr("app.worker.job_downsync", lambda: None)
        mock_logger = Mock()
        monkeypatch.setattr("app.worker.logger", mock_logger)

        asyncio.run(task_downsync({"job_id": "5f46437f49894025acc145be5d22df28"}))

        mock_logger.info.assert_called_once_with(
            "[%s] task_downsync starting (job_id=%s)",
            "triggered",
            "5f46437f49894025acc145be5d22df28",
        )

    def test_still_runs_job_downsync(self, monkeypatch) -> None:
        calls: list[None] = []
        monkeypatch.setattr("app.worker.job_downsync", lambda: calls.append(None))

        asyncio.run(task_downsync({"job_id": "task_downsync:1735689600000"}))

        assert len(calls) == 1
