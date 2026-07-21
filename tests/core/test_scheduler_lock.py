"""Regression tests for the cross-worker scheduler advisory lock.

Production runs vb-api as multiple gunicorn worker processes (see
Dockerfile, --workers 2). Before this lock existed, every worker booted its
own AsyncIOScheduler and registered the same cron jobs, so each scheduled
job (health-check mails, chronicles, backups, ...) fired once per worker
instead of once per deployment — the concrete symptom reported was
archive_health_check/standesdb_health_check mails arriving twice.
"""

from unittest.mock import MagicMock, patch

from apscheduler.schedulers.base import STATE_STOPPED

import app.core.scheduler as scheduler_module


def _teardown_lock_state():
    scheduler_module._scheduler_lock_conn = None


class TestAcquireSchedulerLock:
    def setup_method(self):
        _teardown_lock_state()

    def teardown_method(self):
        _teardown_lock_state()

    def test_non_postgres_dialect_skips_locking(self):
        fake_engine = MagicMock()
        fake_engine.dialect.name = "sqlite"

        with patch.object(scheduler_module, "engine", fake_engine):
            assert scheduler_module._acquire_scheduler_lock() is True

        fake_engine.connect.assert_not_called()

    def test_postgres_lock_acquired_keeps_connection_open(self):
        fake_conn = MagicMock()
        fake_conn.execute.return_value.scalar.return_value = True
        fake_engine = MagicMock()
        fake_engine.dialect.name = "postgresql"
        fake_engine.connect.return_value = fake_conn

        with patch.object(scheduler_module, "engine", fake_engine):
            assert scheduler_module._acquire_scheduler_lock() is True

        fake_conn.close.assert_not_called()
        assert scheduler_module._scheduler_lock_conn is fake_conn

    def test_postgres_lock_held_elsewhere_closes_connection(self):
        fake_conn = MagicMock()
        fake_conn.execute.return_value.scalar.return_value = False
        fake_engine = MagicMock()
        fake_engine.dialect.name = "postgresql"
        fake_engine.connect.return_value = fake_conn

        with patch.object(scheduler_module, "engine", fake_engine):
            assert scheduler_module._acquire_scheduler_lock() is False

        fake_conn.close.assert_called_once()
        assert scheduler_module._scheduler_lock_conn is None


class TestStartSchedulerLockGating:
    def setup_method(self):
        _teardown_lock_state()

    def teardown_method(self):
        _teardown_lock_state()

    def test_start_scheduler_skips_registration_without_lock(self):
        with (
            patch.object(
                scheduler_module, "_acquire_scheduler_lock", return_value=False
            ),
            patch.object(scheduler_module.scheduler, "add_job") as mock_add_job,
            patch.object(scheduler_module.scheduler, "start") as mock_start,
        ):
            scheduler_module.start_scheduler()

        mock_add_job.assert_not_called()
        mock_start.assert_not_called()

    def test_start_scheduler_registers_jobs_when_lock_acquired(self):
        with (
            patch.object(
                scheduler_module, "_acquire_scheduler_lock", return_value=True
            ),
            patch.object(scheduler_module.scheduler, "add_job") as mock_add_job,
            patch.object(scheduler_module.scheduler, "start") as mock_start,
        ):
            scheduler_module.start_scheduler()

        job_ids = [call.kwargs["id"] for call in mock_add_job.call_args_list]
        assert "archive_health_check" in job_ids
        assert "standesdb_health_check" in job_ids
        mock_start.assert_called_once()


class TestStopScheduler:
    def setup_method(self):
        _teardown_lock_state()

    def teardown_method(self):
        _teardown_lock_state()

    def test_stop_scheduler_closes_held_lock_connection(self):
        fake_conn = MagicMock()
        scheduler_module._scheduler_lock_conn = fake_conn
        scheduler_module.scheduler.state = STATE_STOPPED

        scheduler_module.stop_scheduler()

        fake_conn.close.assert_called_once()
        assert scheduler_module._scheduler_lock_conn is None

    def test_stop_scheduler_does_not_shutdown_when_not_running(self):
        # Regression guard: a worker that lost the lock never starts the
        # scheduler, so shutdown() would raise SchedulerNotRunningError on
        # app shutdown without the `scheduler.running` guard.
        scheduler_module.scheduler.state = STATE_STOPPED

        with patch.object(scheduler_module.scheduler, "shutdown") as mock_shutdown:
            scheduler_module.stop_scheduler()

        mock_shutdown.assert_not_called()
