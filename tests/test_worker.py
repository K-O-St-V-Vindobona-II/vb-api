"""Regression tests for app/worker.py's ARQ WorkerSettings.

Job body business logic stays covered by tests/test_scheduled_jobs.py
(the bodies themselves live in app/core/scheduler.py, unchanged) — this
file covers only this module's own wiring: the health-check task, the
per-stage cron_jobs construction, and the timezone/Redis configuration.
"""

import asyncio

from app.core.config import get_settings
from app.core.datetime_utils import get_app_timezone
from app.worker import WorkerSettings, build_cron_jobs, task_health_check


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
