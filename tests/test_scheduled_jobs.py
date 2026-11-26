"""Tests for scheduled jobs — logic verification without email sending.

record_job_run() (app.services.scheduled_task_run_service) opens its own
real SessionLocal() (see its docstring) — like the mailer, its writes
would bypass the db_session fixture's per-test rollback if left
unmocked. mock_record_job_run patches it away for every test in this
file by default (no real scheduled_task_runs rows written), and the
instrumentation-specific tests below request the fixture explicitly to
assert on its call args instead.
"""

import smtplib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import ANY, MagicMock, patch

import pytest

from app.core.datetime_utils import format_date_long, local_today
from app.core.scheduler import (
    MailRun,
    _compute_target_date,
    _import_notice_due,
    _imported_this_month,
    _last_day_of_month,
    _latest_booking,
    _notify_missing_import,
    _parse_month_day,
    _parse_year,
    _reminder_debt,
    _reminders_done_this_month,
    _run_alembic_upgrade_head,
    _send_debtor_reminders,
    _sent_this_month,
    job_archive_health_check,
    job_birthday_mails,
    job_cleanup,
    job_db_backup,
    job_debtor_reminder,
    job_downsync,
    job_refresh_category_filter_hits,
    job_standesdb_chronicles,
    job_standesdb_health_check,
)
from app.models.archive_store_item import ArchiveStoreItem
from app.models.client_user_agent import ClientUserAgent
from app.models.enums import JobId
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.p4x_account import GIROKONTO_ACCOUNT_ID, P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import (
    P4xCategoryFilterHit,
)
from app.models.p4x_transaction import P4xTransaction
from app.models.request_log import RequestLog
from app.models.role import Role
from app.models.scheduled_task_run import ScheduledTaskRun
from app.models.sent_email import SentEmail
from app.models.standesdb_image import StandesdbImage
from app.models.state import State
from app.services.s3_mirror_service import MirrorResult
from tests.p4x.test_p4x_fee_balance import (
    _add_fee_payment,
    _create_fee_member,
)
from tests.p4x.test_p4x_fee_balance import _seed_base as _seed_fee_base


@pytest.fixture(autouse=True)
def mock_record_job_run():
    with patch("app.core.scheduler.record_job_run") as mock:
        yield mock


def _seed_base(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", sort_order=1),
            Org(id="vbn", label="VBN", sort_order=2),
            State(id="fu", label="Fux", sort_order=1),
            State(id="up", label="Urphilister", sort_order=2),
            Role(
                id="phil-x",
                group="philchc",
                label="Phil-x",
                sort_order=1,
            ),
        ]
    )
    db.commit()


def _seed_girokonto(db):
    db.add(
        P4xAccount(
            id=GIROKONTO_ACCOUNT_ID,
            iban="AT61 1904 3002 3457 3201",
            bic="BKAUATWWXXX",
            label="Girokonto",
            init_balance=0,
        )
    )
    db.commit()


class TestHelpers:
    def test_parse_month_day(self):
        assert _parse_month_day("1990-06-15") == (6, 15)
        assert _parse_month_day("2000-12-01") == (12, 1)

    def test_parse_month_day_returns_none_for_malformed_value(self):
        assert _parse_month_day("1990") is None

    def test_parse_year(self):
        assert _parse_year("1990-06-15") == 1990


class TestComputeTargetDate:
    def test_january_rolls_back_to_previous_december(self):
        assert _compute_target_date(date(2026, 1, 15)) == date(2025, 12, 31)

    def test_other_months_use_last_day_of_previous_month(self):
        assert _compute_target_date(date(2026, 7, 1)) == date(2026, 6, 30)


def _add_transaction(db, booking: date, *, deleted: bool = False) -> None:
    account = P4xAccount(iban=f"AT94{booking:%Y%m%d}0000000000", bic="GIBAATWWXXX")
    db.add(account)
    db.commit()
    db.add(
        P4xTransaction(
            sha256_hash=f"tx-{booking}-{deleted}",
            p4x_account_id=account.id,
            booking=booking,
            valuation=booking,
            amount=10,
            subject="booking",
            iban="AT001",
            deleted_at=datetime.now(UTC) if deleted else None,
        )
    )
    db.commit()


def _log_mail(db, template_key: str, sent_at: datetime) -> None:
    db.add(SentEmail(headers=template_key, created_at=sent_at))
    db.commit()


def _seed_treasurer(db) -> Member:
    db.add(Role(id="phil-xxxx", group="philchc", label="Kassier", sort_order=9))
    db.commit()
    treasurer = Member(
        email="kassier@vbw.at",
        vorname="Karl",
        nachname="Kassier",
        couleurname="Fiscus",
        org_id="vbw",
        # not "up", so the treasurer is no fee member who gets a reminder
        state_id="fu",
        entlassen=False,
        verstorben=False,
    )
    db.add(treasurer)
    db.commit()
    db.add(
        MemberRole(
            member_id=treasurer.id,
            role_id="phil-xxxx",
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    return treasurer


class TestDateHelpers:
    def testformat_date_long(self):
        assert format_date_long(date(2026, 1, 5)) == "5. Jänner 2026"

    @pytest.mark.parametrize(
        ("today", "expected"),
        [
            (date(2026, 1, 15), date(2026, 1, 31)),
            (date(2026, 4, 30), date(2026, 4, 30)),
            (date(2028, 2, 1), date(2028, 2, 29)),
            (date(2026, 12, 31), date(2026, 12, 31)),
        ],
    )
    def test_last_day_of_month(self, today, expected):
        assert _last_day_of_month(today) == expected


class TestLatestBooking:
    def test_none_without_transactions(self, db_session):
        assert _latest_booking(db_session) is None

    def test_latest_booking_that_is_not_deleted(self, db_session):
        _add_transaction(db_session, date(2026, 7, 10))
        _add_transaction(db_session, date(2026, 6, 20))
        _add_transaction(db_session, date(2026, 7, 12), deleted=True)

        assert _latest_booking(db_session) == date(2026, 7, 10)


class TestImportedThisMonth:
    @pytest.mark.parametrize(
        ("latest_booking", "expected"),
        [
            (None, False),
            (date(2026, 6, 30), False),
            (date(2025, 7, 20), False),
            (date(2026, 7, 1), True),
        ],
    )
    def test_only_a_booking_of_the_current_month_counts(self, latest_booking, expected):
        assert _imported_this_month(latest_booking, date(2026, 7, 15)) is expected


class TestSentThisMonth:
    def test_local_days_of_one_template_since_the_local_month_start(self, db_session):
        # 23:30 on 30 June in Vienna (summer time, UTC+2): last month
        _log_mail(
            db_session, "debtor_reminder", datetime(2026, 6, 30, 21, 30, tzinfo=UTC)
        )
        # 00:30 on 1 July in Vienna
        _log_mail(
            db_session, "debtor_reminder", datetime(2026, 6, 30, 22, 30, tzinfo=UTC)
        )
        _log_mail(
            db_session, "debtor_reminder", datetime(2026, 7, 16, 16, 30, tzinfo=UTC)
        )
        _log_mail(db_session, "birthday", datetime(2026, 7, 16, 16, 30, tzinfo=UTC))

        assert _sent_this_month(db_session, "debtor_reminder", date(2026, 7, 20)) == [
            date(2026, 7, 1),
            date(2026, 7, 16),
        ]


class TestRemindersDoneThisMonth:
    def test_not_done_without_mails_or_clean_runs(self, db_session):
        _log_mail(
            db_session, "debtor_reminder", datetime(2026, 4, 15, 16, 30, tzinfo=UTC)
        )
        db_session.add(
            ScheduledTaskRun(
                job_id=JobId.DEBTOR_REMINDER,
                started_at=datetime(2026, 7, 15, 16, 30, tzinfo=UTC),
                finished_at=datetime(2026, 7, 15, 16, 30, tzinfo=UTC),
                exit_code=1,
            )
        )
        db_session.commit()

        assert _reminders_done_this_month(db_session, date(2026, 7, 16)) is False

    def test_done_once_reminders_went_out(self, db_session):
        _log_mail(
            db_session, "debtor_reminder", datetime(2026, 7, 15, 16, 30, tzinfo=UTC)
        )

        assert _reminders_done_this_month(db_session, date(2026, 7, 16)) is True

    def test_done_after_a_clean_run_without_debtors(self, db_session):
        db_session.add(
            ScheduledTaskRun(
                job_id=JobId.DEBTOR_REMINDER,
                started_at=datetime(2026, 7, 15, 16, 30, tzinfo=UTC),
                finished_at=datetime(2026, 7, 15, 16, 30, tzinfo=UTC),
                exit_code=0,
            )
        )
        db_session.commit()

        assert _reminders_done_this_month(db_session, date(2026, 7, 16)) is True


class TestImportNoticeDue:
    @pytest.mark.parametrize(
        ("earlier", "today", "expected"),
        [
            ([], date(2026, 7, 15), True),
            ([], date(2026, 7, 28), True),
            ([date(2026, 7, 15)], date(2026, 7, 24), False),
            ([date(2026, 7, 15)], date(2026, 7, 25), True),
            ([date(2026, 7, 25)], date(2026, 7, 26), False),
            ([date(2026, 7, 15), date(2026, 7, 25)], date(2026, 7, 26), False),
        ],
    )
    def test_first_notice_and_one_last_chance(self, earlier, today, expected):
        assert _import_notice_due(earlier, today) is expected


class TestNotifyMissingImport:
    def test_first_notice_goes_to_the_treasurer(self, db_session):
        _seed_base(db_session)
        treasurer = _seed_treasurer(db_session)

        with patch("app.core.scheduler.send_to_recipients") as mock_send:
            note = _notify_missing_import(
                db_session, date(2026, 7, 15), date(2026, 6, 28)
            )

        assert note == "Notice sent."
        kwargs = mock_send.call_args.kwargs
        assert kwargs["to_emails"] == [treasurer.email]
        assert kwargs["subject"] == "Beitragserinnerungen warten auf den Kontoimport"
        assert kwargs["template_key"] == "debtor_reminder_import_missing"
        html = kwargs["html_content"]
        assert f"Hallo {treasurer.cn}," in html
        assert "Stichtag <strong>30. Juni 2026</strong>" in html
        assert "Buchungen nur bis 28. Juni 2026" in html
        assert "täglich bis zum 31. Juli 2026" in html
        assert "Letzte Erinnerung" not in html

    def test_no_second_notice_before_the_last_chance_day(self, db_session):
        _seed_base(db_session)
        _seed_treasurer(db_session)
        _log_mail(
            db_session,
            "debtor_reminder_import_missing",
            datetime(2026, 7, 15, 16, 30, tzinfo=UTC),
        )

        with patch("app.core.scheduler.send_to_recipients") as mock_send:
            note = _notify_missing_import(
                db_session, date(2026, 7, 24), date(2026, 6, 28)
            )

        assert note == "Notice already sent."
        mock_send.assert_not_called()

    def test_last_chance_notice_without_any_import(self, db_session):
        _seed_base(db_session)
        _seed_treasurer(db_session)
        _log_mail(
            db_session,
            "debtor_reminder_import_missing",
            datetime(2026, 7, 15, 16, 30, tzinfo=UTC),
        )

        with patch("app.core.scheduler.send_to_recipients") as mock_send:
            note = _notify_missing_import(db_session, date(2026, 7, 25), None)

        assert note == "Last-chance notice sent."
        kwargs = mock_send.call_args.kwargs
        assert kwargs["subject"] == (
            "Letzte Erinnerung: Beitragserinnerungen warten auf den Kontoimport"
        )
        html = kwargs["html_content"]
        assert "Es ist noch kein Kontoimport vorhanden." in html
        assert "Ohne Kontoimport\nbis zum 31. Juli 2026 entfallen" in html

    def test_nothing_sent_without_a_treasurer(self, db_session):
        _seed_base(db_session)

        with patch("app.core.scheduler.send_to_recipients") as mock_send:
            note = _notify_missing_import(
                db_session, date(2026, 7, 15), date(2026, 6, 28)
            )

        assert note == "No treasurer with an email address to notify."
        mock_send.assert_not_called()


class TestReminderDebt:
    """Real balances: the debt at the cut-off date 30 June 2026, less the
    fee payments booked from 1 July up to the latest booking 17 July."""

    WINDOW = (date(2026, 7, 1), date(2026, 7, 18))

    def _member(self, db, init_balance: int):
        _seed_fee_base(db)
        # freed from fees, so the balance is the initial one plus payments
        return _create_fee_member(
            db,
            p4x_init_date=date(2026, 1, 1),
            p4x_init_balance=init_balance,
            p4x_freed=True,
        )

    def test_payment_after_the_cut_off_cancels_the_reminder(self, db_session):
        member = self._member(db_session, -365)
        _add_fee_payment(db_session, member, date(2026, 7, 10), 395.0)

        assert _reminder_debt(db_session, member, "2026-06-30", self.WINDOW) == 0

    def test_partial_payment_lowers_the_stated_debt(self, db_session):
        member = self._member(db_session, -500)
        _add_fee_payment(db_session, member, date(2026, 7, 17), 100.0)

        assert _reminder_debt(db_session, member, "2026-06-30", self.WINDOW) == 400

    def test_payment_after_the_latest_booking_is_not_credited(self, db_session):
        member = self._member(db_session, -500)
        _add_fee_payment(db_session, member, date(2026, 7, 18), 100.0)

        assert _reminder_debt(db_session, member, "2026-06-30", self.WINDOW) == 500

    def test_payment_on_the_cut_off_date_counts_once(self, db_session):
        member = self._member(db_session, -500)
        _add_fee_payment(db_session, member, date(2026, 6, 30), 100.0)

        assert _reminder_debt(db_session, member, "2026-06-30", self.WINDOW) == 400

    def test_debt_at_the_threshold_is_no_reminder(self, db_session):
        member = self._member(db_session, -300)

        with patch("app.core.scheduler.get_fee_payments_sum") as mock_sum:
            debt = _reminder_debt(db_session, member, "2026-06-30", self.WINDOW)

        assert debt == 0
        mock_sum.assert_not_called()

    def test_no_balance_is_no_reminder(self, db_session):
        member = self._member(db_session, -500)

        with patch("app.core.scheduler.calculate_fee_balance", return_value=None):
            assert _reminder_debt(db_session, member, "2026-06-30", self.WINDOW) == 0


class TestRefreshCategoryFilterHits:
    def test_refresh_clears_and_reapplies(
        self,
        db_session,
        mock_record_job_run,
    ):
        _seed_base(db_session)
        account = P4xAccount(iban="AT941234567890123456", bic="GIBAATWWXXX")
        db_session.add(account)
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
                p4x_account_id=account.id,
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
            p4x_account_id=account.id,
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

    def test_failure_records_exit_code_one(self, db_session, mock_record_job_run):
        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch(
                "app.core.scheduler.apply_all_category_filters",
                side_effect=RuntimeError("filter engine exploded"),
            ),
        ):
            job_refresh_category_filter_hits()  # must not raise

        mock_record_job_run.assert_called_once_with(
            "refresh_category_filter_hits",
            ANY,
            exit_code=1,
            output="filter engine exploded",
        )


def _make_admin_member(db, email: str, role_id: str, role_group: str) -> Member:
    db.add(Role(id=role_id, group=role_group, label=role_id, sort_order=9))
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

    def test_send_failure_records_exit_code_one(
        self, db_session, mock_s3, mock_record_job_run
    ):
        _seed_base(db_session)
        _make_admin_member(
            db_session, "archiveadmin@vbw.at", "internetreferent", "funktion"
        )

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch(
                "app.core.scheduler.send_to_recipients",
                side_effect=RuntimeError("smtp unreachable"),
            ),
        ):
            job_archive_health_check()  # must not raise

        mock_record_job_run.assert_called_once_with(
            "archive_health_check", ANY, exit_code=1, output="smtp unreachable"
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

    def test_send_failure_records_exit_code_one(
        self, db_session, mock_s3, mock_record_job_run
    ):
        _seed_base(db_session)
        _make_admin_member(db_session, "standesdbadmin@vbw.at", "standesfuehrer", "chc")

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch(
                "app.core.scheduler.send_to_recipients",
                side_effect=RuntimeError("smtp unreachable"),
            ),
        ):
            job_standesdb_health_check()  # must not raise

        mock_record_job_run.assert_called_once_with(
            "standesdb_health_check", ANY, exit_code=1, output="smtp unreachable"
        )


class TestRunAlembicUpgradeHead:
    """alembic upgrade head must run as a subprocess, never in-process -
    see _run_alembic_upgrade_head()'s docstring for why (alembic/env.py's
    fileConfig() call silently disables this module's own logger after
    the first in-process invocation, observed to also swallow this job's
    own scheduled_task_runs completion write)."""

    def test_success_runs_expected_command(self):
        with patch("app.core.scheduler.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _run_alembic_upgrade_head()

        mock_run.assert_called_once_with(
            ["alembic", "upgrade", "head"], capture_output=True, check=False
        )

    def test_failure_raises_with_stderr(self):
        with patch("app.core.scheduler.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stderr=b"FAILED: could not connect to database"
            )
            with pytest.raises(RuntimeError, match="could not connect to database"):
                _run_alembic_upgrade_head()


class TestJobDownsync:
    def test_happy_path_calls_steps_in_order(self, mock_record_job_run):
        with (
            patch(
                "app.core.scheduler.build_prod_storage", return_value=MagicMock()
            ) as mock_build_storage,
            patch(
                "app.core.scheduler.mirror_prefix",
                return_value=MirrorResult(synced=["a"]),
            ) as mock_mirror,
            patch("app.core.scheduler.run_restore") as mock_restore,
            patch("app.core.scheduler._run_alembic_upgrade_head") as mock_upgrade,
        ):
            mock_restore.return_value = "production-2026-08-08_03-00-00.dump"
            job_downsync()

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

    def test_light_setting_mirrors_through_mirror_light_only(
        self, monkeypatch, mock_record_job_run
    ):
        monkeypatch.setenv("DOWNSYNC_LIGHT", "true")
        with (
            patch("app.core.scheduler.build_prod_storage", return_value=MagicMock()),
            patch(
                "app.core.scheduler.mirror_light",
                return_value=MirrorResult(synced=["a", "b"]),
            ) as mock_light,
            patch("app.core.scheduler.mirror_prefix") as mock_full,
            patch(
                "app.core.scheduler.run_restore",
                return_value="production-2026-08-08_03-00-00.dump",
            ),
            patch("app.core.scheduler._run_alembic_upgrade_head"),
        ):
            job_downsync()

        mock_light.assert_called_once()
        mock_full.assert_not_called()
        mock_record_job_run.assert_called_once_with(
            "downsync",
            ANY,
            exit_code=0,
            output=(
                "S3-Files (light): 2 synced, 0 skipped, 0 deleted; DB: restored "
                "from production-2026-08-08_03-00-00.dump."
            ),
        )

    def test_mirror_errors_skip_restore_and_migration(self, mock_record_job_run):
        with (
            patch("app.core.scheduler.build_prod_storage", return_value=MagicMock()),
            patch(
                "app.core.scheduler.mirror_prefix",
                return_value=MirrorResult(errors=["archive/store/x"]),
            ),
            patch("app.core.scheduler.run_restore") as mock_restore,
            patch("app.core.scheduler._run_alembic_upgrade_head") as mock_upgrade,
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
                "app.core.scheduler.build_prod_storage",
                side_effect=RuntimeError("no creds"),
            ),
            patch("app.core.scheduler.mirror_prefix") as mock_mirror,
        ):
            job_downsync()  # must not raise

        mock_mirror.assert_not_called()
        mock_record_job_run.assert_called_once_with(
            "downsync", ANY, exit_code=1, output=ANY
        )

    def test_mirror_prefix_exception_is_caught_and_logged(self, mock_record_job_run):
        with (
            patch("app.core.scheduler.build_prod_storage", return_value=MagicMock()),
            patch(
                "app.core.scheduler.mirror_prefix",
                side_effect=RuntimeError("s3 connection reset"),
            ),
            patch("app.core.scheduler.run_restore") as mock_restore,
        ):
            job_downsync()  # must not raise

        mock_restore.assert_not_called()
        mock_record_job_run.assert_called_once_with(
            "downsync",
            ANY,
            exit_code=1,
            output="S3 mirror failed: s3 connection reset",
        )

    def test_restore_exception_is_caught_and_logged(self, mock_record_job_run):
        with (
            patch("app.core.scheduler.build_prod_storage", return_value=MagicMock()),
            patch("app.core.scheduler.mirror_prefix", return_value=MirrorResult()),
            patch("app.core.scheduler.run_restore", side_effect=RuntimeError("boom")),
            patch("app.core.scheduler._run_alembic_upgrade_head") as mock_upgrade,
        ):
            job_downsync()  # must not raise

        mock_upgrade.assert_not_called()
        mock_record_job_run.assert_called_once_with(
            "downsync", ANY, exit_code=1, output=ANY
        )

    def test_production_guard_refuses_to_run(self, monkeypatch, mock_record_job_run):
        monkeypatch.setenv("APP_ENVIRONMENT", "production")
        with patch("app.core.scheduler.build_prod_storage") as mock_build_storage:
            job_downsync()

        mock_build_storage.assert_not_called()
        # Deliberately untracked — see app/core/scheduler.py's job_downsync
        # docstring: this guard should never actually trigger in practice.
        mock_record_job_run.assert_not_called()


class TestStandesdbChronicles:
    def test_recipients_sent_via_bcc_not_to(self, db_session, mock_record_job_run):
        _seed_base(db_session)
        today = local_today()
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
        assert kwargs["subject"] == "Verbindungschroniken"
        assert kwargs["template_key"] == "chronicles"
        assert "feiert seinen" in kwargs["html_content"]
        mock_record_job_run.assert_called_once_with(
            "standesdb_chronicles",
            ANY,
            exit_code=0,
            output="Chronicles sent to 1 recipient(s).",
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

    def test_short_mail_when_no_anniversaries(self, db_session, mock_record_job_run):
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

        mock_send.assert_called_once()
        assert (
            "stehen keine Jahrestage an" in mock_send.call_args.kwargs["html_content"]
        )
        mock_record_job_run.assert_called_once_with(
            "standesdb_chronicles",
            ANY,
            exit_code=0,
            output="Chronicles without anniversaries sent to 1 recipient(s).",
        )

    def test_send_failure_records_exit_code_one(self, db_session, mock_record_job_run):
        _seed_base(db_session)
        today = local_today()
        dow = today.isoweekday()
        week_start = today + timedelta(days=(8 - dow) % 7)
        target = week_start + timedelta(days=1)

        db_session.add(
            Member(
                email="chronik2@vbw.at",
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
        )
        db_session.commit()

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch(
                "app.core.scheduler.send_to_recipients",
                side_effect=RuntimeError("smtp unreachable"),
            ),
        ):
            job_standesdb_chronicles()  # must not raise

        mock_record_job_run.assert_called_once_with(
            "standesdb_chronicles", ANY, exit_code=1, output="smtp unreachable"
        )


class TestBirthdayMails:
    def test_sends_with_personal_from_name(self, db_session, mock_record_job_run):
        _seed_base(db_session)
        tomorrow = local_today() + timedelta(days=1)

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

    def test_send_failure_records_exit_code_one(self, db_session, mock_record_job_run):
        _seed_base(db_session)
        tomorrow = local_today() + timedelta(days=1)

        db_session.add(
            Member(
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
        )
        db_session.commit()

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch(
                "app.core.scheduler.send_to_recipients",
                side_effect=RuntimeError("smtp unreachable"),
            ),
        ):
            job_birthday_mails()  # must not raise

        mock_record_job_run.assert_called_once_with(
            "birthday_mails", ANY, exit_code=1, output="smtp unreachable"
        )


class TestBirthdayMailDeliveryFailures:
    def _add_birthday_member(self, db, email: str, name: str) -> None:
        tomorrow = local_today() + timedelta(days=1)
        db.add(
            Member(
                email=email,
                vorname=name,
                nachname="User",
                couleurname=name,
                org_id="vbw",
                state_id="fu",
                geburtsdatum=date(1990, tomorrow.month, tomorrow.day),
                geburtsdatum_accuracy=3,
                entlassen=False,
                verstorben=False,
                zustellungen="adresse_privat",
            )
        )

    def _run_job(self, db_session, deliver):
        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch(
                "app.core.scheduler.send_to_recipients", side_effect=deliver
            ) as mock_send,
        ):
            job_birthday_mails()
        return mock_send

    def test_one_refused_mail_does_not_stop_the_others(
        self, db_session, mock_record_job_run
    ):
        _seed_base(db_session)
        self._add_birthday_member(db_session, "a@vbw.at", "Anton")
        self._add_birthday_member(db_session, "b@vbw.at", "Berta")
        db_session.commit()

        def deliver(*, to_emails, **_kwargs):
            if to_emails == ["a@vbw.at"]:
                raise smtplib.SMTPRecipientsRefused({"a@vbw.at": (550, b"no")})

        mock_send = self._run_job(db_session, deliver)

        assert mock_send.call_count == 2
        mock_record_job_run.assert_called_once_with(
            "birthday_mails",
            ANY,
            exit_code=1,
            output="1 birthday mail(s) sent. 1 failed: Anton User v/o Anton.",
        )

    def test_a_dropped_connection_is_a_delivery_failure_too(
        self, db_session, mock_record_job_run
    ):
        _seed_base(db_session)
        self._add_birthday_member(db_session, "a@vbw.at", "Anton")
        self._add_birthday_member(db_session, "b@vbw.at", "Berta")
        db_session.commit()

        mock_send = self._run_job(db_session, ConnectionRefusedError("no route"))

        assert mock_send.call_count == 2
        output = mock_record_job_run.call_args.kwargs["output"]
        assert mock_record_job_run.call_args.kwargs["exit_code"] == 1
        assert output.startswith("0 birthday mail(s) sent. 2 failed: ")
        assert "Anton User v/o Anton" in output
        assert "Berta User v/o Berta" in output

    def test_all_mails_sent_records_exit_code_zero(
        self, db_session, mock_record_job_run
    ):
        _seed_base(db_session)
        self._add_birthday_member(db_session, "a@vbw.at", "Anton")
        self._add_birthday_member(db_session, "b@vbw.at", "Berta")
        db_session.commit()

        self._run_job(db_session, None)

        mock_record_job_run.assert_called_once_with(
            "birthday_mails", ANY, exit_code=0, output="2 birthday mail(s) sent."
        )

    def test_any_other_error_still_ends_the_job(self, db_session, mock_record_job_run):
        _seed_base(db_session)
        self._add_birthday_member(db_session, "a@vbw.at", "Anton")
        self._add_birthday_member(db_session, "b@vbw.at", "Berta")
        db_session.commit()

        mock_send = self._run_job(db_session, RuntimeError("settings missing"))

        assert mock_send.call_count == 1
        mock_record_job_run.assert_called_once_with(
            "birthday_mails", ANY, exit_code=1, output="settings missing"
        )


class TestMailRun:
    def test_summary_lists_the_failures(self):
        run = MailRun(sent=2, failed=["A", "B"])

        assert run.exit_code == 1
        assert run.summary("mail(s)") == "2 mail(s) sent. 2 failed: A, B."

    def test_a_clean_run_has_exit_code_zero(self):
        run = MailRun(sent=3)

        assert run.exit_code == 0
        assert run.summary("mail(s)") == "3 mail(s) sent."

    def test_runs_do_not_share_their_list_of_failures(self):
        first = MailRun()
        first.failed.append("A")

        assert MailRun().failed == []


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

    def test_purges_orphaned_client_user_agents(self, db_session, mock_record_job_run):
        """Once an old RequestLog gets purged, any ClientUserAgent that
        RequestLog was the sole referrer of becomes orphaned and must be
        swept in the same run - not left behind indefinitely."""
        very_old = datetime.now(UTC) - timedelta(days=3650)
        now = datetime.now(UTC)
        orphan_agent = ClientUserAgent(string="orphan-ua")
        referenced_agent = ClientUserAgent(string="still-referenced-ua")
        db_session.add_all([orphan_agent, referenced_agent])
        db_session.commit()

        db_session.add_all(
            [
                # Old enough to be purged - the request that used to keep
                # orphan_agent alive.
                RequestLog(
                    client_ip="127.0.0.1",
                    client_ips=["127.0.0.1"],
                    client_user_agent_id=orphan_agent.id,
                    request_method="GET",
                    request_path="/",
                    response_status=200,
                    created_at=very_old,
                    updated_at=very_old,
                ),
                # Recent - keeps referenced_agent non-orphaned even after
                # the purge above runs.
                RequestLog(
                    client_ip="127.0.0.1",
                    client_ips=["127.0.0.1"],
                    client_user_agent_id=referenced_agent.id,
                    request_method="GET",
                    request_path="/",
                    response_status=200,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        db_session.commit()
        orphan_agent_id = orphan_agent.id
        referenced_agent_id = referenced_agent.id

        with (
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
        ):
            job_cleanup()

        remaining_ids = {ua.id for ua in db_session.query(ClientUserAgent).all()}
        assert orphan_agent_id not in remaining_ids
        assert referenced_agent_id in remaining_ids
        mock_record_job_run.assert_called_once_with(
            "cleanup", ANY, exit_code=0, output=ANY
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
            job_db_backup()

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
            job_db_backup()  # must not raise

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
            job_db_backup()

        mock_record_job_run.assert_called_once_with(
            "db_backup", ANY, exit_code=0, output=ANY
        )
        assert (
            "retention cleanup failed" in mock_record_job_run.call_args.kwargs["output"]
        )

    def test_expired_backups_are_noted_in_output(self, mock_record_job_run):
        with (
            patch("app.core.scheduler.get_storage", return_value=MagicMock()),
            patch(
                "app.core.scheduler.run_backup",
                return_value="development-2026-08-04_03-00-00.dump",
            ),
            patch(
                "app.core.scheduler.cleanup_old_backups",
                return_value=["development-2026-01-01_03-00-00.dump"],
            ),
        ):
            job_db_backup()

        output = mock_record_job_run.call_args.kwargs["output"]
        assert "1 expired backup(s) cleaned up" in output


class TestDebtorReminder:
    def test_sends_with_treasurers_real_name(self, db_session):
        _seed_base(db_session)
        _seed_girokonto(db_session)
        db_session.add(
            Role(id="phil-xxxx", group="philchc", label="Kassier", sort_order=9)
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
            patch(
                "app.core.scheduler.get_fee_payments_sum", return_value=Decimal(0)
            ) as mock_paid_since,
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            _send_debtor_reminders(
                db_session,
                date(2026, 6, 30),
                "2026-06-30",
                payments_until=date(2026, 7, 17),
            )

        mock_paid_since.assert_called_once_with(
            db_session, ANY, date(2026, 7, 1), date(2026, 7, 18)
        )
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["to_emails"] == [debtor.email]
        assert kwargs["from_name"] == treasurer.cn
        html = kwargs["html_content"]
        assert "Per <strong>30. Juni 2026</strong>" in html
        assert '<strong style="color: red;">400 Euro.</strong>' in html
        assert "Zahlungseingänge bis 17. Juli 2026 sind dabei berücksichtigt." in html

    def test_falls_back_to_generic_sender_name_without_a_treasurer(self, db_session):
        """_get_phil_xxxx_name()'s "no role holder found" branch: with no
        phil-xxxx member seeded, the reminder must still go out under a
        generic sender name instead of crashing."""
        _seed_base(db_session)
        _seed_girokonto(db_session)
        debtor = Member(
            email="schuldner2@vbw.at",
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
            _send_debtor_reminders(
                db_session,
                date(2026, 6, 30),
                "2026-06-30",
                payments_until=date(2026, 7, 17),
            )

        assert mock_send.call_args.kwargs["from_name"] == "Philisterkassier"

    def test_skips_members_without_balance_data_or_below_threshold(self, db_session):
        _seed_base(db_session)
        no_balance = Member(
            email="keine-daten@vbw.at",
            vorname="Ohne",
            nachname="Daten",
            couleurname="Nihil",
            org_id="vbw",
            state_id="up",
            entlassen=False,
            verstorben=False,
        )
        below_threshold = Member(
            email="wenig-schulden@vbw.at",
            vorname="Wenig",
            nachname="Schulden",
            couleurname="Minor",
            org_id="vbw",
            state_id="up",
            entlassen=False,
            verstorben=False,
        )
        no_email = Member(
            email=None,
            vorname="Ohne",
            nachname="Adresse",
            couleurname="Mutus",
            org_id="vbw",
            state_id="up",
            entlassen=False,
            verstorben=False,
        )
        db_session.add_all([no_balance, below_threshold, no_email])
        db_session.commit()

        def fake_balance(_db, member, *_args, **_kwargs):
            if member.id == no_balance.id:
                return None
            if member.id == no_email.id:
                return {"end_balance": -400.0}
            return {"end_balance": -50.0}  # below the 300 threshold

        with (
            patch("app.core.scheduler.fee_for_month", return_value=15.0),
            patch(
                "app.core.scheduler.calculate_fee_balance",
                side_effect=fake_balance,
            ),
            patch("app.core.scheduler.send_to_recipients") as mock_send,
        ):
            _send_debtor_reminders(
                db_session,
                date(2026, 6, 30),
                "2026-06-30",
                payments_until=date(2026, 7, 17),
            )

        mock_send.assert_not_called()

    def test_done_this_month_is_not_tracked(self, db_session, mock_record_job_run):
        """Every later evening of the month only finds the job done: no
        second round of reminders and no entry in the run history."""
        _log_mail(
            db_session, "debtor_reminder", datetime(2026, 7, 15, 16, 30, tzinfo=UTC)
        )
        with (
            patch("app.core.scheduler.local_today", return_value=date(2026, 7, 16)),
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler._send_debtor_reminders") as mock_send_reminders,
        ):
            job_debtor_reminder()

        mock_send_reminders.assert_not_called()
        mock_record_job_run.assert_not_called()

    def test_missing_import_notifies_and_records_exit_code_one(
        self, db_session, mock_record_job_run
    ):
        _add_transaction(db_session, date(2026, 6, 28))
        with (
            patch("app.core.scheduler.local_today", return_value=date(2026, 7, 15)),
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch(
                "app.core.scheduler._notify_missing_import",
                return_value="Notice sent.",
            ) as mock_notify,
            patch("app.core.scheduler._send_debtor_reminders") as mock_send_reminders,
        ):
            job_debtor_reminder()

        mock_notify.assert_called_once_with(
            db_session, date(2026, 7, 15), date(2026, 6, 28)
        )
        mock_send_reminders.assert_not_called()
        mock_record_job_run.assert_called_once_with(
            "debtor_reminder",
            ANY,
            exit_code=1,
            output=(
                "Latest booking 2026-06-28 is before this month, import missing."
                " Notice sent."
            ),
        )

    def test_no_import_at_all_is_recorded_as_none(
        self, db_session, mock_record_job_run
    ):
        with (
            patch("app.core.scheduler.local_today", return_value=date(2026, 7, 15)),
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch(
                "app.core.scheduler._notify_missing_import",
                return_value="Notice sent.",
            ),
        ):
            job_debtor_reminder()

        output = mock_record_job_run.call_args.kwargs["output"]
        assert output.startswith("Latest booking none is before this month")

    def test_success_records_exit_code_zero(self, db_session, mock_record_job_run):
        _add_transaction(db_session, date(2026, 7, 10))
        with (
            patch("app.core.scheduler.local_today", return_value=date(2026, 7, 15)),
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch(
                "app.core.scheduler._send_debtor_reminders", return_value=MailRun()
            ) as mock_send_reminders,
        ):
            job_debtor_reminder()

        mock_send_reminders.assert_called_once_with(
            db_session,
            date(2026, 6, 30),
            "2026-06-30",
            payments_until=date(2026, 7, 10),
        )
        mock_record_job_run.assert_called_once_with(
            "debtor_reminder",
            ANY,
            exit_code=0,
            output=(
                "Target date 2026-06-30, payments until 2026-07-10: 0 reminder(s) sent."
            ),
        )

    def test_one_refused_reminder_does_not_stop_the_others(self, db_session):
        _seed_base(db_session)
        _seed_girokonto(db_session)
        debtors = [
            Member(
                email=f"schuldner-{name}@vbw.at",
                vorname=name,
                nachname="Schuldner",
                couleurname=name,
                org_id="vbw",
                state_id="up",
                entlassen=False,
                verstorben=False,
            )
            for name in ("Anton", "Berta")
        ]
        db_session.add_all(debtors)
        db_session.commit()

        disconnected = smtplib.SMTPServerDisconnected()

        def deliver(*, to_emails, **_kwargs):
            if to_emails == ["schuldner-Anton@vbw.at"]:
                raise disconnected

        with (
            patch("app.core.scheduler.fee_for_month", return_value=15.0),
            patch(
                "app.core.scheduler.calculate_fee_balance",
                return_value={"end_balance": -400.0},
            ),
            patch(
                "app.core.scheduler.send_to_recipients", side_effect=deliver
            ) as mock_send,
        ):
            run = _send_debtor_reminders(
                db_session,
                date(2026, 6, 30),
                "2026-06-30",
                payments_until=date(2026, 7, 17),
            )

        assert mock_send.call_count == 2
        assert run.sent == 1
        assert run.failed == ["Anton Schuldner v/o Anton"]

    def test_failed_reminders_record_exit_code_one_with_a_summary(
        self, db_session, mock_record_job_run
    ):
        _add_transaction(db_session, date(2026, 7, 10))
        with (
            patch("app.core.scheduler.local_today", return_value=date(2026, 7, 15)),
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch(
                "app.core.scheduler._send_debtor_reminders",
                return_value=MailRun(sent=1, failed=["Anton"]),
            ),
        ):
            job_debtor_reminder()

        mock_record_job_run.assert_called_once_with(
            "debtor_reminder",
            ANY,
            exit_code=1,
            output=(
                "Target date 2026-06-30, payments until 2026-07-10:"
                " 1 reminder(s) sent. 1 failed: Anton."
            ),
        )

    def test_send_reminders_failure_records_exit_code_one(
        self, db_session, mock_record_job_run
    ):
        with (
            patch("app.core.scheduler.local_today", return_value=date(2026, 7, 15)),
            patch("app.core.scheduler.SessionLocal", return_value=db_session),
            patch.object(db_session, "close"),
            patch("app.core.scheduler._latest_booking", return_value=date(2026, 7, 10)),
            patch(
                "app.core.scheduler._send_debtor_reminders",
                side_effect=RuntimeError("mailer down"),
            ),
        ):
            job_debtor_reminder()  # must not raise

        mock_record_job_run.assert_called_once_with(
            "debtor_reminder", ANY, exit_code=1, output="mailer down"
        )
