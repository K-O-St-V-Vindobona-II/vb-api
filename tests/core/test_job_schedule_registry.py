"""Regression tests for app/core/job_schedule_registry.py.

This module is the single source of truth both the ARQ worker (real cron
registration, see app/worker.py::build_cron_jobs) and the web container
(read-only introspection, see get_scheduled_jobs() below) must agree on —
these tests focus on the filtering/introspection logic itself, not on any
job body's business logic (already covered by tests/test_scheduled_jobs.py).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from arq.cron import next_cron

from app.core.config import get_settings
from app.core.job_schedule_registry import (
    JOB_REGISTRY,
    applicable_entries,
    get_scheduled_jobs,
)


class TestApplicableEntries:
    def test_production_includes_business_jobs_and_backup_excludes_downsync(self):
        ids = [
            e.id
            for e in applicable_entries(
                app_environment="production", backup_enabled=True
            )
        ]
        assert ids == [
            "cleanup",
            "refresh_category_filter_hits",
            "birthday_mails",
            "debtor_reminder",
            "standesdb_chronicles",
            "archive_health_check",
            "standesdb_health_check",
            "db_backup",
        ]

    def test_non_production_only_has_cleanup_and_downsync(self):
        ids = [
            e.id
            for e in applicable_entries(app_environment="test", backup_enabled=True)
        ]
        assert ids == ["cleanup", "downsync"]

    def test_backup_disabled_removes_db_backup_in_production(self):
        ids = [
            e.id
            for e in applicable_entries(
                app_environment="production", backup_enabled=False
            )
        ]
        assert "db_backup" not in ids
        assert "cleanup" in ids


class TestGetScheduledJobs:
    def test_returns_one_entry_per_applicable_job_in_production(self, monkeypatch):
        monkeypatch.setenv("APP_ENVIRONMENT", "production")
        get_settings.cache_clear()

        jobs = get_scheduled_jobs()

        expected_ids = {
            e.id
            for e in applicable_entries(
                app_environment="production", backup_enabled=True
            )
        }
        assert {j["id"] for j in jobs} == expected_ids

    def test_response_shape_matches_frontend_contract(self):
        jobs = get_scheduled_jobs()
        cleanup = next(j for j in jobs if j["id"] == "cleanup")

        assert cleanup["name"] == "job_cleanup"
        assert cleanup["trigger"] == "cron[minute='0']"
        assert cleanup["description"]
        assert cleanup["next_run"] is not None

    def test_weekly_job_trigger_description_includes_day_of_week(self, monkeypatch):
        # standesdb_chronicles only registers in production.
        monkeypatch.setenv("APP_ENVIRONMENT", "production")
        get_settings.cache_clear()

        jobs = get_scheduled_jobs()

        chronicles = next(j for j in jobs if j["id"] == "standesdb_chronicles")
        assert (
            chronicles["trigger"] == "cron[day_of_week='tues', hour='17', minute='0']"
        )


class TestJobRegistryEntries:
    def test_registry_has_exactly_nine_jobs(self):
        assert len(JOB_REGISTRY) == 9

    def test_next_run_is_dst_aware_across_a_spring_forward_transition(self):
        # Europe/Vienna springs forward on the last Sunday of March. A
        # naive fixed-offset implementation would silently drift by an
        # hour here; ZoneInfo-based next_cron() must not.
        vienna = ZoneInfo("Europe/Vienna")
        birthday_entry = next(e for e in JOB_REGISTRY if e.id == "birthday_mails")

        before_transition = datetime(2026, 3, 28, 0, 0, tzinfo=vienna)
        next_run = next_cron(
            before_transition,
            day=birthday_entry.day,
            weekday=birthday_entry.weekday,
            hour=birthday_entry.hour,
            minute=birthday_entry.minute,
        )

        assert next_run.hour == 15
        assert next_run.minute == 53
