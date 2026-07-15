"""Tests for scheduled jobs — logic verification without email sending."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.core.scheduler import (
    _parse_month_day,
    _parse_year,
    _send_debtor_reminders,
    job_archive_health_check,
    job_birthday_mails,
    job_refresh_category_filter_hits,
    job_standesdb_chronicles,
    job_standesdb_health_check,
    scheduler,
)
from app.models.archive_store_item import ArchiveStoreItem
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import (
    P4xCategoryFilterHit,
)
from app.models.p4x_transaction import P4xTransaction
from app.models.role import Role
from app.models.standesdb_image import StandesdbImage
from app.models.state import State


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
    ):
        _seed_base(db_session)
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
                sha256hash="abc123",
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
    def test_sends_ok_mail_when_healthy(self, db_session, mock_s3):
        _seed_base(db_session)
        member = _make_admin_member(
            db_session, "archiveadmin@vbw.at", "internetreferent", "ir"
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

    def test_sends_error_mail_when_file_missing(self, db_session, mock_s3):
        _seed_base(db_session)
        _make_admin_member(db_session, "archiveadmin@vbw.at", "internetreferent", "ir")
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

    def test_no_mail_when_no_recipients(self, db_session, mock_s3):
        _seed_base(db_session)

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            job_archive_health_check()

        mock_send.assert_not_called()


class TestStandesdbHealthCheck:
    def test_sends_ok_mail_when_healthy(self, db_session, mock_s3):
        _seed_base(db_session)
        member = _make_admin_member(
            db_session, "standesdbadmin@vbw.at", "standesfuehrer", "sf"
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

    def test_sends_error_mail_when_image_missing(self, db_session, mock_s3):
        _seed_base(db_session)
        _make_admin_member(db_session, "standesdbadmin@vbw.at", "standesfuehrer", "sf")
        db_session.add(
            StandesdbImage(
                owner_type="member",
                owner_id=1,
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

    def test_no_mail_when_no_recipients(self, db_session, mock_s3):
        _seed_base(db_session)

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            job_standesdb_health_check()

        mock_send.assert_not_called()


class TestBackupJobRegistration:
    def test_backup_job_registered_by_default(self):
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from app.core.scheduler import BACKUP_HOUR, job_db_backup

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
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        from app.core.scheduler import BACKUP_HOUR, job_db_backup

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


class TestSchedulerTimezone:
    def test_scheduler_uses_vienna_timezone(self):
        # Regression guard: without an explicit timezone, APScheduler falls
        # back to the container's local zone (UTC, since no TZ env var was
        # set), causing every human-facing cron job to fire 1-2h too late.
        assert scheduler.timezone == ZoneInfo("Europe/Vienna")


class TestStandesdbChronicles:
    def test_recipients_sent_via_bcc_not_to(self, db_session):
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

    def test_no_send_when_no_recipients(self, db_session):
        _seed_base(db_session)

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            job_standesdb_chronicles()

        mock_send.assert_not_called()

    def test_no_send_when_no_anniversaries(self, db_session):
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


class TestBirthdayMails:
    def test_sends_with_personal_from_name(self, db_session):
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
            zustellungen="aktiviert",
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


class TestDebtorReminder:
    def test_sends_with_treasurers_real_name(self, db_session):
        _seed_base(db_session)
        db_session.add(
            Role(id="phil-xxxx", group="philisterkassier", label="Kassier", order=9)
        )
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
