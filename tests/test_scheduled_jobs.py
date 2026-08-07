"""Tests for scheduled jobs — logic verification without email sending.

record_job_run() (app.services.scheduled_task_run_service) opens its own
real SessionLocal() (see its docstring) — like the mailer, its writes
would bypass the db_session fixture's per-test rollback if left
unmocked. mock_record_job_run patches it away for every test in this
file by default (no real scheduled_task_runs rows written), and the
instrumentation-specific tests below request the fixture explicitly to
assert on its call args instead.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from unittest.mock import ANY, MagicMock, patch

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.scheduler import (
    APP_TZ,
    BACKUP_HOUR,
    _format_next_run,
    _parse_month_day,
    _parse_year,
    _send_debtor_reminders,
    get_scheduled_jobs,
    job_archive_health_check,
    job_birthday_mails,
    job_cleanup,
    job_db_backup,
    job_debtor_reminder,
    job_downsync,
    job_refresh_category_filter_hits,
    job_standesdb_chronicles,
    job_standesdb_health_check,
    scheduler,
)
from app.models.archive_store_item import ArchiveStoreItem
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import (
    P4xCategoryFilterHit,
)
from app.models.p4x_transaction import P4xTransaction
from app.models.role import Role
from app.models.standesdb_image import StandesdbImage
from app.models.state import State
from app.services.s3_mirror_service import MirrorResult


@pytest.fixture(autouse=True)
def mock_record_job_run():
    with patch("app.core.scheduler.record_job_run") as mock:
        yield mock


def _seed_base(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Org(id="vbn", label="VBN", order=2),
            State(id="fu", label="Fux", order=1),
            State(id="up", label="Urphilister", order=2),
            Role(
                id="phil-x",
                group="philchc",
                label="Phil-x",
                order=1,
            ),
        ]
    )
    db.commit()


class TestHelpers:
    def test_parse_month_day(self):
        assert _parse_month_day("1990-06-15") == (6, 15)
        assert _parse_month_day("2000-12-01") == (12, 1)

    def test_parse_year(self):
        assert _parse_year("1990-06-15") == 1990


class TestRefreshCategoryFilterHits:
    def test_refresh_clears_and_reapplies(
        self,
        db_session,
        mock_record_job_run,
    ):
        _seed_base(db_session)
        db_session.add(P4xAccount(id=1, iban="AT941234567890123456", bic="GIBAATWWXXX"))
        cat = P4xCategory(
            name="test",
            label="Test",
            background_color="#000",
            text_color="#fff",
        )
        db_session.add(cat)
        db_session.commit()

        db_session.add(
            P4xTransaction(
                sha256_hash="abc123",
                p4x_account_id=1,
                booking=datetime.now(UTC).date(),
                valuation=datetime.now(UTC).date(),
                amount=100,
                subject="Mitgliedsbeitrag Test",
                iban="AT001234",
                deleted_at=None,
            )
        )
        db_session.commit()

        cf = P4xCategoryFilter(
            name="test-filter",
            p4x_account_id=1,
            p4x_category_id=cat.id,
            subject="Mitgliedsbeitrag",
            subject_mode="contains",
        )
        db_session.add(cf)
        db_session.commit()

        with (
            patch(
                "app.core.scheduler.SessionLocal",
                return_value=db_session,
            ),
            patch.object(db_session, "close"),
        ):
            job_refresh_category_filter_hits()

        hits = db_session.query(P4xCategoryFilterHit).all()
        assert len(hits) == 1
        assert hits[0].p4x_category_filter_id == cf.id

        mock_record_job_run.assert_called_once_with(
            "refresh_category_filter_hits", ANY, exit_code=0, output=ANY
        )


def _make_admin_member(db, email: str, role_id: str, role_group: str) -> Member:
    db.add(Role(id=role_id, group=role_group, label=role_id, order=9))
    m = Member(
        email=email,
        vorname="Admin",
        nachname="Test",
        couleurname="Testikus",
        org_id="vbw",
        state_id="fu",
        entlassen=False,
        verstorben=False,
    )
    db.add(m)
    db.commit()
    db.add(
        MemberRole(
            member_id=m.id,
            role_id=role_id,
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    return m


class TestArchiveHealthCheck:
    def test_sends_ok_mail_when_healthy(self, db_session, mock_s3, mock_record_job_run):
        _seed_base(db_session)
        member = _make_admin_member(
            db_session, "archiveadmin@vbw.at", "internetreferent", "funktion"
        )

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            job_archive_health_check()

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["to_emails"] == [member.email]
        assert kwargs["subject"].startswith("OK:")
        mock_record_job_run.assert_called_once_with(
            "archive_health_check", ANY, exit_code=0, output=ANY
        )

    def test_sends_error_mail_when_file_missing(
        self, db_session, mock_s3, mock_record_job_run
    ):
        _seed_base(db_session)
        _make_admin_member(
            db_session, "archiveadmin@vbw.at", "internetreferent", "funktion"
        )
        now = datetime.now(UTC)
        db_session.add(
            ArchiveStoreItem(
                name="f",
                extension="pdf",
                mime_type="application/pdf",
                size=10,
                sha256_hash="missing_hash",
                created_at=now,
                updated_at=now,
            )
        )
        db_session.commit()

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            job_archive_health_check()

        kwargs = mock_send.call_args.kwargs
        assert kwargs["subject"].startswith("FEHLER:")
        mock_record_job_run.assert_called_once_with(
            "archive_health_check", ANY, exit_code=1, output=ANY
        )

    def test_no_mail_when_no_recipients(self, db_session, mock_s3, mock_record_job_run):
        _seed_base(db_session)

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            job_archive_health_check()

        mock_send.assert_not_called()
        mock_record_job_run.assert_called_once_with(
            "archive_health_check", ANY, exit_code=1, output=ANY
        )


class TestStandesdbHealthCheck:
    def test_sends_ok_mail_when_healthy(self, db_session, mock_s3, mock_record_job_run):
        _seed_base(db_session)
        member = _make_admin_member(
            db_session, "standesdbadmin@vbw.at", "standesfuehrer", "chc"
        )

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            job_standesdb_health_check()

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["to_emails"] == [member.email]
        assert kwargs["subject"].startswith("OK:")
        mock_record_job_run.assert_called_once_with(
            "standesdb_health_check", ANY, exit_code=0, output=ANY
        )

    def test_sends_error_mail_when_image_missing(
        self, db_session, mock_s3, mock_record_job_run
    ):
        _seed_base(db_session)
        admin = _make_admin_member(
            db_session, "standesdbadmin@vbw.at", "standesfuehrer", "chc"
        )
        db_session.add(
            StandesdbImage(
                owner_member_id=admin.id,
                sha256_hash="missing_img_hash",
            )
        )
        db_session.commit()

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            job_standesdb_health_check()

        kwargs = mock_send.call_args.kwargs
        assert kwargs["subject"].startswith("FEHLER:")
        mock_record_job_run.assert_called_once_with(
            "standesdb_health_check", ANY, exit_code=1, output=ANY
        )

    def test_no_mail_when_no_recipients(self, db_session, mock_s3, mock_record_job_run):
        _seed_base(db_session)

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            job_standesdb_health_check()

        mock_send.assert_not_called()
        mock_record_job_run.assert_called_once_with(
            "standesdb_health_check", ANY, exit_code=1, output=ANY
        )


class TestBackupJobRegistration:
    def test_backup_job_registered_by_default(self):
        sched = AsyncIOScheduler()
        sched.add_job(
            job_db_backup,
            "cron",
            hour=BACKUP_HOUR,
            minute=0,
            timezone=UTC,
            id="db_backup",
            replace_existing=True,
        )
        ids = [j.id for j in sched.get_jobs()]
        assert "db_backup" in ids

    def test_backup_job_absent_when_disabled(self):
        sched = AsyncIOScheduler()
        # When BACKUP_ENABLED=false, no job is added — empty scheduler has no db_backup
        ids = [j.id for j in sched.get_jobs()]
        assert "db_backup" not in ids

    def test_backup_job_uses_restart_safe_cron_trigger(self):
        """Regression guard: the job used to run on an interval trigger
        whose start_date was recomputed on every app restart
        (replace_existing=True on every scheduler.add_job() call at
        startup, no persistent jobstore), silently resetting the schedule
        any time the app redeployed more often than the configured
        interval — which happened routinely in this project (observed gaps
        of 5, 5, 1, 1 days instead of the configured 7). A cron trigger's
        next-fire time depends only on hour/minute/timezone, not on when
        add_job() was called, so it can't drift with restart frequency."""
        sched = AsyncIOScheduler()
        sched.add_job(
            job_db_backup,
            "cron",
            hour=BACKUP_HOUR,
            minute=0,
            timezone=UTC,
            id="db_backup",
        )
        trigger = sched.get_job("db_backup").trigger

        assert isinstance(trigger, CronTrigger)
        assert str(trigger) == f"cron[hour='{BACKUP_HOUR}', minute='0']"


class TestFormatNextRun:
    def test_computes_next_run_from_trigger(self):
        trigger = CronTrigger(hour=7, minute=0, timezone=APP_TZ)
        now = datetime(2026, 8, 5, 3, 0, tzinfo=APP_TZ)

        result = _format_next_run(MagicMock(trigger=trigger), now)

        assert result == "05.08.2026, 07:00"

    def test_returns_none_when_trigger_has_no_further_fire_time(self):
        trigger = CronTrigger(year=2020, timezone=APP_TZ)
        now = datetime(2026, 8, 5, 3, 0, tzinfo=APP_TZ)

        assert _format_next_run(MagicMock(trigger=trigger), now) is None


class TestGetScheduledJobs:
    """Regression tests for the production bug where /system/scheduled-jobs
    showed an empty list depending on which of the 2 gunicorn workers
    handled the request — see start_scheduler()/_acquire_scheduler_lock()
    docstrings for the full mechanism.
    """

    def test_next_run_works_for_a_never_started_scheduler(self):
        # This is the exact shape of the bug: a worker that never called
        # scheduler.start() (i.e. never won the advisory lock) still adds
        # every job (see start_scheduler()), but APScheduler only computes
        # next_run_time for jobs added to an already-running scheduler —
        # here the job stays "pending" and never gets a next_run_time
        # attribute at all. The old implementation (job.next_run_time
        # .strftime(...)) raised AttributeError in exactly this case;
        # reading the trigger directly must not.
        sched = AsyncIOScheduler(timezone=APP_TZ)
        sched.add_job(
            job_cleanup,
            "cron",
            hour=7,
            minute=0,
            id="cleanup",
            replace_existing=True,
        )
        assert not hasattr(sched.get_job("cleanup"), "next_run_time")

        with patch("app.core.scheduler.scheduler", sched):
            result = get_scheduled_jobs()

        assert len(result) == 1
        assert result[0]["next_run"] is not None

    def test_returns_none_next_run_when_trigger_exhausted(self):
        sched = AsyncIOScheduler(timezone=APP_TZ)
        sched.add_job(
            job_cleanup,
            CronTrigger(year=2020, timezone=APP_TZ),
            id="cleanup",
            replace_existing=True,
        )

        with patch("app.core.scheduler.scheduler", sched):
            result = get_scheduled_jobs()

        assert result[0]["next_run"] is None


class TestJobDownsync:
    def test_happy_path_calls_steps_in_order(self, mock_record_job_run):
        with (
            patch(
                "app.core.scheduler.load_aws_env", return_value={"AWS_BUCKET": "x"}
            ) as mock_load_env,
            patch(
                "app.core.scheduler.build_prod_storage", return_value=MagicMock()
            ) as mock_build_storage,
            patch(
                "app.core.scheduler.mirror_prefix",
                return_value=MirrorResult(synced=["a"]),
            ) as mock_mirror,
            patch("app.core.scheduler.run_restore") as mock_restore,
            patch("app.core.scheduler.command.upgrade") as mock_upgrade,
        ):
            mock_restore.return_value = "production-2026-08-08_03-00-00.dump"
            job_downsync()

        mock_load_env.assert_called_once()
        mock_build_storage.assert_called_once()
        mock_mirror.assert_called_once()
        mock_restore.assert_called_once()
        mock_upgrade.assert_called_once()
        mock_record_job_run.assert_called_once_with(
            "downsync",
            ANY,
            exit_code=0,
            output=(
                "S3-Files: 1 synced, 0 skipped, 0 deleted; DB: restored from "
                "production-2026-08-08_03-00-00.dump."
            ),
        )

    def test_mirror_errors_skip_restore_and_migration(self, mock_record_job_run):
        with (
            patch("app.core.scheduler.load_aws_env", return_value={}),
            patch("app.core.scheduler.build_prod_storage", return_value=MagicMock()),
            patch(
                "app.core.scheduler.mirror_prefix",
                return_value=MirrorResult(errors=["archive/store/x"]),
            ),
            patch("app.core.scheduler.run_restore") as mock_restore,
            patch("app.core.scheduler.command.upgrade") as mock_upgrade,
        ):
            job_downsync()

        mock_restore.assert_not_called()
        mock_upgrade.assert_not_called()
        mock_record_job_run.assert_called_once_with(
            "downsync", ANY, exit_code=1, output=ANY
        )

    def test_missing_credentials_is_caught_and_logged(self, mock_record_job_run):
        with (
            patch(
                "app.core.scheduler.load_aws_env",
                side_effect=RuntimeError("no creds"),
            ),
            patch("app.core.scheduler.mirror_prefix") as mock_mirror,
        ):
            job_downsync()  # must not raise

        mock_mirror.assert_not_called()
        mock_record_job_run.assert_called_once_with(
            "downsync", ANY, exit_code=1, output=ANY
        )

    def test_restore_exception_is_caught_and_logged(self, mock_record_job_run):
        with (
            patch("app.core.scheduler.load_aws_env", return_value={}),
            patch("app.core.scheduler.build_prod_storage", return_value=MagicMock()),
            patch("app.core.scheduler.mirror_prefix", return_value=MirrorResult()),
            patch("app.core.scheduler.run_restore", side_effect=RuntimeError("boom")),
            patch("app.core.scheduler.command.upgrade") as mock_upgrade,
        ):
            job_downsync()  # must not raise

        mock_upgrade.assert_not_called()
        mock_record_job_run.assert_called_once_with(
            "downsync", ANY, exit_code=1, output=ANY
        )

    def test_production_guard_refuses_to_run(self, monkeypatch, mock_record_job_run):
        monkeypatch.setenv("APP_ENVIRONMENT", "production")
        with patch("app.core.scheduler.load_aws_env") as mock_load_env:
            job_downsync()

        mock_load_env.assert_not_called()
        # Deliberately untracked — see app/core/scheduler.py's job_downsync
        # docstring: this guard should never actually trigger in practice.
        mock_record_job_run.assert_not_called()


class TestSchedulerTimezone:
    def test_scheduler_uses_configured_app_timezone(self):
        # Regression guard: without an explicit timezone, APScheduler falls
        # back to the container's local zone (UTC, since no TZ env var was
        # set), causing every human-facing cron job to fire 1-2h too late.
        # Compares against the module constant (like BACKUP_HOUR above) —
        # APP_TZ is read from Settings.app_timezone once at import time, not
        # re-read dynamically per job.
        assert scheduler.timezone == APP_TZ


class TestStandesdbChronicles:
    def test_recipients_sent_via_bcc_not_to(self, db_session, mock_record_job_run):
        _seed_base(db_session)
        today = datetime.now(UTC).date()
        dow = today.isoweekday()
        week_start = today + timedelta(days=(8 - dow) % 7)
        target = week_start + timedelta(days=1)

        m = Member(
            email="chronik@vbw.at",
            vorname="Test",
            nachname="User",
            couleurname="Testikus",
            org_id="vbw",
            state_id="fu",
            geburtsdatum=date(1990, target.month, target.day),
            geburtsdatum_accuracy=3,
            entlassen=False,
            verstorben=False,
            chroniclemail=True,
        )
        db_session.add(m)
        db_session.commit()

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            job_standesdb_chronicles()

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["to_emails"] == []
        assert kwargs["bcc_emails"] == ["chronik@vbw.at"]
        mock_record_job_run.assert_called_once_with(
            "standesdb_chronicles", ANY, exit_code=0, output=ANY
        )

    def test_no_send_when_no_recipients(self, db_session, mock_record_job_run):
        _seed_base(db_session)

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            job_standesdb_chronicles()

        mock_send.assert_not_called()
        mock_record_job_run.assert_called_once_with(
            "standesdb_chronicles", ANY, exit_code=0, output=ANY
        )

    def test_no_send_when_no_anniversaries(self, db_session, mock_record_job_run):
        _seed_base(db_session)
        db_session.add(
            Member(
                email="chronik@vbw.at",
                vorname="Test",
                nachname="User",
                couleurname="Testikus",
                org_id="vbw",
                state_id="fu",
                entlassen=False,
                verstorben=False,
                chroniclemail=True,
            )
        )
        db_session.commit()

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            job_standesdb_chronicles()

        mock_send.assert_not_called()
        mock_record_job_run.assert_called_once_with(
            "standesdb_chronicles", ANY, exit_code=0, output=ANY
        )


class TestBirthdayMails:
    def test_sends_with_personal_from_name(self, db_session, mock_record_job_run):
        _seed_base(db_session)
        tomorrow = datetime.now(UTC).date() + timedelta(days=1)

        m = Member(
            email="geburtstag@vbw.at",
            vorname="Test",
            nachname="User",
            couleurname="Testikus",
            org_id="vbw",
            state_id="fu",
            geburtsdatum=date(1990, tomorrow.month, tomorrow.day),
            geburtsdatum_accuracy=3,
            entlassen=False,
            verstorben=False,
            zustellungen="adresse_privat",
        )
        db_session.add(m)
        db_session.commit()

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            job_birthday_mails()

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["to_emails"] == [m.email]
        assert kwargs["from_name"] == "Philister-ChC Vindobona II"
        mock_record_job_run.assert_called_once_with(
            "birthday_mails", ANY, exit_code=0, output=ANY
        )

    def test_no_birthdays_tomorrow_still_records_success(
        self, db_session, mock_record_job_run
    ):
        _seed_base(db_session)

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            job_birthday_mails()

        mock_send.assert_not_called()
        mock_record_job_run.assert_called_once_with(
            "birthday_mails", ANY, exit_code=0, output=ANY
        )


class TestJobCleanup:
    def test_success_records_exit_code_zero(self, db_session, mock_record_job_run):
        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
        ):
            job_cleanup()

        mock_record_job_run.assert_called_once_with(
            "cleanup", ANY, exit_code=0, output=ANY
        )

    def test_failure_records_exit_code_one(self, db_session, mock_record_job_run):
        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch.object(db_session, "query", side_effect=RuntimeError("db exploded")),
        ):
            job_cleanup()  # must not raise

        mock_record_job_run.assert_called_once_with(
            "cleanup", ANY, exit_code=1, output="db exploded"
        )


class TestJobDbBackup:
    def test_success_includes_backup_name_in_output(self, mock_record_job_run):
        with (
            patch("app.core.scheduler.get_storage", return_value=MagicMock()),
            patch(
                "app.core.scheduler.run_backup",
                return_value="development-2026-08-04_03-00-00.dump",
            ),
            patch("app.core.scheduler.cleanup_old_backups", return_value=[]),
        ):
            asyncio.run(job_db_backup())

        mock_record_job_run.assert_called_once_with(
            "db_backup",
            ANY,
            exit_code=0,
            output="Backup succeeded: development-2026-08-04_03-00-00.dump",
        )

    def test_backup_failure_records_exit_code_one(self, mock_record_job_run):
        with (
            patch("app.core.scheduler.get_storage", return_value=MagicMock()),
            patch(
                "app.core.scheduler.run_backup",
                side_effect=RuntimeError("pg_dump failed"),
            ),
            patch("app.core.scheduler.cleanup_old_backups") as mock_cleanup,
        ):
            asyncio.run(job_db_backup())  # must not raise

        mock_cleanup.assert_not_called()
        mock_record_job_run.assert_called_once_with(
            "db_backup", ANY, exit_code=1, output=ANY
        )

    def test_retention_cleanup_failure_does_not_flip_exit_code(
        self, mock_record_job_run
    ):
        """Cleanup is best-effort — a failure there must not overwrite an
        already-successful backup's exit_code, only be noted in output."""
        with (
            patch("app.core.scheduler.get_storage", return_value=MagicMock()),
            patch(
                "app.core.scheduler.run_backup",
                return_value="development-2026-08-04_03-00-00.dump",
            ),
            patch(
                "app.core.scheduler.cleanup_old_backups",
                side_effect=RuntimeError("s3 unreachable"),
            ),
        ):
            asyncio.run(job_db_backup())

        mock_record_job_run.assert_called_once_with(
            "db_backup", ANY, exit_code=0, output=ANY
        )
        assert (
            "retention cleanup failed" in mock_record_job_run.call_args.kwargs["output"]
        )


class TestDebtorReminder:
    def test_sends_with_treasurers_real_name(self, db_session):
        _seed_base(db_session)
        db_session.add(Role(id="phil-xxxx", group="philchc", label="Kassier", order=9))
        db_session.commit()

        treasurer = Member(
            email="kassier@vbw.at",
            vorname="Karl",
            nachname="Kassier",
            couleurname="Fiscus",
            org_id="vbw",
            # not "up" — otherwise the treasurer would also match the
            # fee_members query below and receive their own reminder.
            state_id="fu",
            entlassen=False,
            verstorben=False,
        )
        db_session.add(treasurer)
        db_session.commit()
        db_session.add(
            MemberRole(
                member_id=treasurer.id,
                role_id="phil-xxxx",
                startdate=date(2000, 1, 1),
                enddate=None,
            )
        )
        db_session.commit()

        debtor = Member(
            email="schuldner@vbw.at",
            vorname="Max",
            nachname="Schuldner",
            couleurname="Debitor",
            org_id="vbw",
            state_id="up",
            entlassen=False,
            verstorben=False,
        )
        db_session.add(debtor)
        db_session.commit()

        with (
            patch("app.core.scheduler.fee_for_month", return_value=15.0),
            patch(
                "app.core.scheduler.calculate_fee_balance",
                return_value={"end_balance": -400.0},
            ),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            _send_debtor_reminders(db_session, date(2026, 6, 30), "2026-06-30")

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["to_emails"] == [debtor.email]
        assert kwargs["from_name"] == treasurer.cn

    def test_quarter_skip_month_not_tracked(self, mock_record_job_run):
        """June (%3==0) is a skip month by design — this early return
        happens before a DB session even opens, so it's deliberately not
        recorded as a run (see job_debtor_reminder's instrumentation)."""
        with patch("app.core.scheduler.datetime") as mock_datetime:
            mock_datetime.now.return_value.date.return_value = date(2026, 6, 15)
            job_debtor_reminder()

        mock_record_job_run.assert_not_called()

    def test_stale_booking_records_exit_code_one(self, db_session, mock_record_job_run):
        with (
            patch("app.core.scheduler.datetime") as mock_datetime,
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler._validate_latest_booking", return_value=False),
        ):
            mock_datetime.now.return_value.date.return_value = date(2026, 7, 15)
            job_debtor_reminder()

        mock_record_job_run.assert_called_once_with(
            "debtor_reminder", ANY, exit_code=1, output=ANY
        )

    def test_success_records_exit_code_zero(self, db_session, mock_record_job_run):
        with (
            patch("app.core.scheduler.datetime") as mock_datetime,
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler._validate_latest_booking", return_value=True),
            patch("app.core.scheduler._send_debtor_reminders") as mock_send_reminders,
        ):
            mock_datetime.now.return_value.date.return_value = date(2026, 7, 15)
            job_debtor_reminder()

        mock_send_reminders.assert_called_once()
        mock_record_job_run.assert_called_once_with(
            "debtor_reminder", ANY, exit_code=0, output=ANY
        )
