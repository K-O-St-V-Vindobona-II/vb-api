"""Job bodies for every scheduled (cron) job and the manual downsync
re-run, run by the dedicated ARQ worker process (see app/worker.py, which
wraps each function below in a thin async task and wires it into either
arq's cron_jobs or its ad-hoc-enqueueable functions — see
app/core/job_schedule_registry.py for the declarative "when does each job
run" data).

Every job function below catches a broad `Exception` around its body by
design: jobs run unattended, and one job's failure must never block the
worker from picking up the next job. Each catch logs via
`logger.exception(...)` (not `logger.warning`), which Ruff's BLE001
specifically exempts from the blind-except check since it preserves the
full traceback rather than silently swallowing it.
"""

import logging
import subprocess
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.datetime_utils import local_today
from app.core.mailer import render_template, send_to_recipients
from app.core.security import (
    REFRESH_TOKEN_LIFETIME_DAYS,
    SESSION_IDLE_TIMEOUT_MINUTES,
)
from app.core.storage import get_storage
from app.core.tasks import TRACKING_RETENTION_MONTHS
from app.db.database import SessionLocal
from app.models.auth_session import AuthSession
from app.models.client_user_agent import ClientUserAgent
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.p4x_transaction import P4xTransaction
from app.models.password_reset import PasswordResetToken
from app.models.request_log import RequestLog
from app.models.sent_email import SentEmail
from app.services.anniversary_service import (
    compute_anniversaries,
    format_date_de,
    get_opted_in_recipients,
    week_window,
)
from app.services.archive_service import get_unsorted_upload_count
from app.services.backup_service import cleanup_old_backups, run_backup, run_restore
from app.services.downsync_service import build_prod_storage
from app.services.p4x_category_service import apply_all_category_filters
from app.services.p4x_fee_balance_service import calculate_fee_balance, fee_for_month
from app.services.permission_service import get_emails_with_permission
from app.services.s3_mirror_service import mirror_prefix
from app.services.scheduled_task_run_service import record_job_run
from app.services.storage_integrity_service import (
    check_archive_integrity,
    check_standesdb_integrity,
)

logger = logging.getLogger(__name__)

MONTHS_DE = [
    "",
    "Jänner",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
]


# -------------------------------------------------------------------
# Cleanup: expired tokens, old logs — hourly
# -------------------------------------------------------------------


def job_cleanup() -> None:
    db = SessionLocal()
    started = datetime.now(UTC)
    try:
        now = datetime.now(UTC)

        max_lifetime = now - timedelta(
            days=REFRESH_TOKEN_LIFETIME_DAYS,
        )
        db.query(AuthSession).filter(
            AuthSession.created_at < max_lifetime,
        ).delete()

        idle = now - timedelta(
            minutes=SESSION_IDLE_TIMEOUT_MINUTES,
        )
        db.query(AuthSession).filter(
            AuthSession.last_used_at < idle,
        ).delete()

        reset_expiry = now - timedelta(minutes=20)
        db.query(PasswordResetToken).filter(
            PasswordResetToken.created_at < reset_expiry,
        ).delete()

        tracking_cutoff = now - timedelta(
            days=TRACKING_RETENTION_MONTHS * 30,
        )
        deleted_logs = (
            db.query(RequestLog)
            .filter(
                RequestLog.created_at < tracking_cutoff,
            )
            .delete()
        )
        deleted_emails = (
            db.query(SentEmail)
            .filter(
                SentEmail.created_at < tracking_cutoff,
            )
            .delete()
        )

        if deleted_logs or deleted_emails:
            orphan_ids = (
                db.query(ClientUserAgent.id)
                .outerjoin(
                    RequestLog,
                    ClientUserAgent.id == RequestLog.client_user_agent_id,
                )
                .filter(RequestLog.id.is_(None))
                .all()
            )
            if orphan_ids:
                db.query(ClientUserAgent).filter(
                    ClientUserAgent.id.in_([r[0] for r in orphan_ids]),
                ).delete(synchronize_session=False)

            logger.info(
                "Cleanup: %d logs, %d emails removed (cutoff: %s)",
                deleted_logs,
                deleted_emails,
                tracking_cutoff.date(),
            )

        db.commit()
        record_job_run(
            "cleanup",
            started,
            exit_code=0,
            output=f"{deleted_logs} logs, {deleted_emails} emails removed",
        )
    except Exception as exc:
        logger.exception("Cleanup failed")
        record_job_run("cleanup", started, exit_code=1, output=str(exc))
    finally:
        db.close()


# -------------------------------------------------------------------
# Task 1: RefreshCategoryFilterHits — daily 07:00
# -------------------------------------------------------------------


def job_refresh_category_filter_hits() -> None:
    db = SessionLocal()
    started = datetime.now(UTC)
    try:
        apply_all_category_filters(db, truncate_first=True)
        logger.info("Category filter hits refreshed.")
        record_job_run(
            "refresh_category_filter_hits",
            started,
            exit_code=0,
            output="Category filter hits refreshed.",
        )
    except Exception as exc:
        logger.exception("RefreshCategoryFilterHits failed")
        record_job_run(
            "refresh_category_filter_hits", started, exit_code=1, output=str(exc)
        )
    finally:
        db.close()


# -------------------------------------------------------------------
# Task 2: BirthdayMails — daily 15:53
# -------------------------------------------------------------------


def job_birthday_mails() -> None:
    db = SessionLocal()
    started = datetime.now(UTC)
    try:
        tomorrow = local_today() + timedelta(days=1)

        members = (
            db.query(Member)
            .filter(
                Member.org_id == "vbw",
                Member.entlassen == False,  # noqa: E712
                Member.verstorben == False,  # noqa: E712
                Member.geburtsdatum_accuracy >= 3,
                Member.zustellungen != "deaktiviert",
                Member.email.isnot(None),
                Member.email != "",
                Member.couleurname.isnot(None),
                Member.couleurname != "",
            )
            .all()
        )

        birthday_members = [
            m
            for m in members
            if m.geburtsdatum
            and _parse_month_day(m.geburtsdatum) == (tomorrow.month, tomorrow.day)
        ]

        if not birthday_members:
            record_job_run(
                "birthday_mails", started, exit_code=0, output="No birthdays tomorrow."
            )
            return

        bcc_emails = _get_role_holder_emails(
            db,
            ["phil-x", "phil-xxx"],
            "vbw",
        )

        for m in birthday_members:
            if not m.email:
                continue
            birth_year = _parse_year(m.geburtsdatum)
            age = tomorrow.year - birth_year if birth_year else "?"
            html = render_template(
                "birthday.html",
                name=m.couleurname,
                age=age,
            )
            send_to_recipients(
                to_emails=[m.email],
                subject="Geburtstagsgruß Deiner Bundesbrüder",
                html_content=html,
                template_key="birthday",
                from_addr="philchc@vindobona2.at",
                from_name="Philister-ChC Vindobona II",
                reply_to="philchc@vindobona2.at",
                bcc_emails=bcc_emails,
            )
            logger.info(
                "Birthday mail sent to %s",
                m.cn,
            )
        record_job_run(
            "birthday_mails",
            started,
            exit_code=0,
            output=f"{len(birthday_members)} birthday mail(s) sent.",
        )
    except Exception as exc:
        logger.exception("BirthdayMails failed")
        record_job_run("birthday_mails", started, exit_code=1, output=str(exc))
    finally:
        db.close()


def _parse_month_day(
    d: object,
) -> tuple[int, int] | None:
    s = str(d)
    parts = s.split("-")
    if len(parts) >= 3:
        return int(parts[1]), int(parts[2])
    return None


def _parse_year(d: object) -> int | None:
    s = str(d)
    parts = s.split("-")
    if parts:
        return int(parts[0])
    return None


def _get_role_holder_emails(
    db: Session,
    role_ids: list[str],
    org_id: str,
) -> list[str]:
    today = local_today()
    member_ids = {
        mr.member_id
        for mr in db.query(MemberRole)
        .filter(
            MemberRole.role_id.in_(role_ids),
            MemberRole.startdate <= today,
            (MemberRole.enddate.is_(None)) | (MemberRole.enddate > today),
        )
        .all()
    }
    if not member_ids:
        return []
    return [
        m.email
        for m in db.query(Member)
        .filter(
            Member.id.in_(member_ids),
            Member.org_id == org_id,
            Member.email.isnot(None),
            Member.email != "",
        )
        .all()
        if m.email
    ]


# -------------------------------------------------------------------
# Task 3: DebtorReminder — monthly on 25th, every 3 months
# -------------------------------------------------------------------


def _validate_latest_booking(db: Session, today: date) -> bool:
    latest_tx = (
        db.query(P4xTransaction)
        .filter(P4xTransaction.deleted_at.is_(None))
        .order_by(P4xTransaction.booking.desc())
        .first()
    )
    if not latest_tx or not latest_tx.booking:
        logger.warning("DebtorReminder: no transactions found.")
        return False

    latest_booking = str(latest_tx.booking)[:7]
    current_month = today.strftime("%Y-%m")
    if latest_booking != current_month:
        logger.warning(
            "DebtorReminder: latest transaction too old (%s). Import missing.",
            latest_booking,
        )
        return False
    return True


def _compute_target_date(today: date) -> date:
    if today.month == 1:
        return date(today.year - 1, 12, 31)
    next_month_first = date(today.year, today.month, 1)
    return next_month_first - timedelta(days=1)


def _send_debtor_reminders(
    db: Session,
    target: date,
    target_str: str,
) -> None:
    monthly_fee = fee_for_month(db, target)
    sender_name = _get_phil_xxxx_name(db)
    sender_email = _get_phil_xxxx_email(db)
    bcc_emails = _get_role_holder_emails(db, ["phil-x", "phil-xxxx"], "vbw")
    target_formatted = f"{target.day}. {MONTHS_DE[target.month]} {target.year}"

    fee_members = (
        db.query(Member)
        .filter(
            Member.org_id == "vbw",
            Member.state_id == "up",
            Member.entlassen == False,  # noqa: E712
            Member.verstorben == False,  # noqa: E712
        )
        .all()
    )

    for m in fee_members:
        balance_data = calculate_fee_balance(db, m, None, target_str)
        if not balance_data:
            continue
        end_balance = float(balance_data.get("end_balance", 0))
        debt = int(-end_balance) if end_balance < 0 else 0

        if debt <= 300 or not m.email:
            continue

        html = render_template(
            "debtor_reminder.html",
            name=m.couleurname or m.cn,
            fee=monthly_fee,
            target=target_formatted,
            debt=debt,
            sender_name=sender_name,
        )
        send_to_recipients(
            to_emails=[m.email],
            subject="Erinnerung an Deine Mitgliedsbeiträge",
            html_content=html,
            template_key="debtor_reminder",
            from_addr="philisterkassier@vindobona2.at",
            from_name=sender_name,
            reply_to=sender_email,
            bcc_emails=bcc_emails,
        )
        logger.info("Debtor reminder sent to %s (debt: %d)", m.cn, debt)


def job_debtor_reminder() -> None:
    today = local_today()
    if today.month % 3 == 0:
        return

    db = SessionLocal()
    started = datetime.now(UTC)
    try:
        if not _validate_latest_booking(db, today):
            record_job_run(
                "debtor_reminder",
                started,
                exit_code=1,
                output="Latest transaction booking is too old — import missing.",
            )
            return

        target = _compute_target_date(today)
        target_str = target.strftime("%Y-%m-%d")
        _send_debtor_reminders(db, target, target_str)
        record_job_run(
            "debtor_reminder",
            started,
            exit_code=0,
            output=f"Debtor reminders sent for target date {target_str}.",
        )
    except Exception as exc:
        logger.exception("DebtorReminder failed")
        record_job_run("debtor_reminder", started, exit_code=1, output=str(exc))
    finally:
        db.close()


def _get_phil_xxxx_name(db: Session) -> str:
    holder = _get_role_holder_emails(
        db,
        ["phil-xxxx"],
        "vbw",
    )
    if not holder:
        return "Philisterkassier"
    m = db.query(Member).filter(Member.email == holder[0]).first()
    return m.cn if m else "Philisterkassier"


def _get_phil_xxxx_email(
    db: Session,
) -> str | None:
    holder = _get_role_holder_emails(
        db,
        ["phil-xxxx"],
        "vbw",
    )
    return holder[0] if holder else None


# -------------------------------------------------------------------
# Task 4: StandesdbChronicles — weekly Tuesday 17:00
# -------------------------------------------------------------------


def job_standesdb_chronicles() -> None:
    db = SessionLocal()
    started = datetime.now(UTC)
    try:
        bcc_emails = get_opted_in_recipients(db)
        if not bcc_emails:
            record_job_run(
                "standesdb_chronicles",
                started,
                exit_code=0,
                output="No opted-in recipients.",
            )
            return

        given = local_today()
        anniversaries = compute_anniversaries(db, given)
        if not anniversaries:
            record_job_run(
                "standesdb_chronicles",
                started,
                exit_code=0,
                output="No anniversaries this week.",
            )
            return

        week_start, week_end = week_window(given)
        html = render_template(
            "chronicles.html",
            anniversaries=anniversaries,
            start=format_date_de(week_start),
            end=format_date_de(week_end),
        )
        send_to_recipients(
            to_emails=[],
            bcc_emails=bcc_emails,
            subject="Verbindungschroniken",
            html_content=html,
            template_key="chronicles",
        )
        logger.info(
            "Chronicles sent to %d recipients.",
            len(bcc_emails),
        )
        record_job_run(
            "standesdb_chronicles",
            started,
            exit_code=0,
            output=f"Chronicles sent to {len(bcc_emails)} recipient(s).",
        )
    except Exception as exc:
        logger.exception("Chronicles failed")
        record_job_run("standesdb_chronicles", started, exit_code=1, output=str(exc))
    finally:
        db.close()


# -------------------------------------------------------------------
# Task 5: ArchiveHealthCheck — weekly Tuesday 01:00
# -------------------------------------------------------------------


def _health_check_subject(feature: str, *, is_healthy: bool) -> str:
    status = "OK" if is_healthy else "FEHLER"
    return f"{status}: VB:{feature}:Konsistenzprüfung"


def job_archive_health_check() -> None:
    db = SessionLocal()
    started = datetime.now(UTC)
    try:
        to_emails = get_emails_with_permission(db, "archiveAdmin")
        if not to_emails:
            logger.warning("ArchiveHealthCheck: no archiveAdmin recipients found.")
            record_job_run(
                "archive_health_check",
                started,
                exit_code=1,
                output="No archiveAdmin recipients found.",
            )
            return

        storage = get_storage()
        report = check_archive_integrity(db, storage)
        unsorted_count = get_unsorted_upload_count(db)

        subject = _health_check_subject("Archiv", is_healthy=report.is_healthy)
        html = render_template(
            "archive_health_check.html",
            missing=report.missing,
            orphans=report.orphans,
            unsorted_count=unsorted_count,
        )
        send_to_recipients(
            to_emails=to_emails,
            subject=subject,
            html_content=html,
            template_key="archive_health_check",
        )
        logger.info(
            "Archive health check sent to %d recipient(s)"
            " (%d missing, %d orphans, %d unsorted).",
            len(to_emails),
            len(report.missing),
            len(report.orphans),
            unsorted_count,
        )
        record_job_run(
            "archive_health_check",
            started,
            exit_code=0 if report.is_healthy else 1,
            output=(
                f"{len(report.missing)} missing, {len(report.orphans)} orphans, "
                f"{unsorted_count} unsorted."
            ),
        )
    except Exception as exc:
        logger.exception("ArchiveHealthCheck failed")
        record_job_run("archive_health_check", started, exit_code=1, output=str(exc))
    finally:
        db.close()


# -------------------------------------------------------------------
# Task 6: StandesdbHealthCheck — weekly Tuesday 03:00
# -------------------------------------------------------------------


def job_standesdb_health_check() -> None:
    db = SessionLocal()
    started = datetime.now(UTC)
    try:
        to_emails = get_emails_with_permission(db, "standesdbVbwAdmin")
        if not to_emails:
            logger.warning(
                "StandesdbHealthCheck: no standesdbVbwAdmin recipients found."
            )
            record_job_run(
                "standesdb_health_check",
                started,
                exit_code=1,
                output="No standesdbVbwAdmin recipients found.",
            )
            return

        storage = get_storage()
        report = check_standesdb_integrity(db, storage)

        subject = _health_check_subject("Standesdb", is_healthy=report.is_healthy)
        html = render_template(
            "standesdb_health_check.html",
            missing=report.missing,
            orphans=report.orphans,
        )
        send_to_recipients(
            to_emails=to_emails,
            subject=subject,
            html_content=html,
            template_key="standesdb_health_check",
        )
        logger.info(
            "Standesdb health check sent to %d recipient(s) (%d missing, %d orphans).",
            len(to_emails),
            len(report.missing),
            len(report.orphans),
        )
        record_job_run(
            "standesdb_health_check",
            started,
            exit_code=0 if report.is_healthy else 1,
            output=f"{len(report.missing)} missing, {len(report.orphans)} orphans.",
        )
    except Exception as exc:
        logger.exception("StandesdbHealthCheck failed")
        record_job_run("standesdb_health_check", started, exit_code=1, output=str(exc))
    finally:
        db.close()


# -------------------------------------------------------------------
# DB backup
# -------------------------------------------------------------------


def job_db_backup() -> None:
    storage = get_storage()
    started = datetime.now(UTC)
    try:
        backup_name = run_backup(storage)
        logger.info("Scheduled DB backup succeeded: %s", backup_name)
    except Exception as exc:
        logger.exception("Scheduled DB backup failed.")
        record_job_run(
            "db_backup", started, exit_code=1, output=f"Backup failed: {exc}"
        )
        return

    output = f"Backup succeeded: {backup_name}"
    try:
        deleted = cleanup_old_backups(storage)
        if deleted:
            logger.info("Cleaned up %d expired backup(s).", len(deleted))
            output += f"; {len(deleted)} expired backup(s) cleaned up."
    except Exception as exc:
        logger.exception("Backup retention cleanup failed.")
        output += f"; retention cleanup failed: {exc}"

    record_job_run("db_backup", started, exit_code=0, output=output)


# -------------------------------------------------------------------
# Downsync (non-production only): mirror prod S3 down, then restore the
# freshest backup locally — keeps every non-prod stage's data roughly
# current with production once a day.
# -------------------------------------------------------------------


def _run_alembic_upgrade_head() -> None:
    """Run `alembic upgrade head` as a subprocess, never in-process.

    docker-entrypoint.sh and scripts/downsync_prod.py already run alembic
    this way - this matches them rather than calling alembic's own
    command.upgrade() Python API directly, which is what this job used to
    do. That in-process call runs alembic/env.py, which calls
    logging.config.fileConfig(alembic.ini) on every invocation -
    alembic.ini's [loggers] section only lists root/sqlalchemy/alembic,
    so fileConfig's default disable_existing_loggers=True silently
    disables every other already-configured logger (this module's
    included) for the rest of the worker process's lifetime. Observed in
    practice: after the first successful downsync, this job's own
    completion log line - and its own record_job_run() call right after
    it - silently stopped happening on every later run in the same
    process, with no exception and no trace. A subprocess is a disposable
    interpreter; whatever logging state it reconfigures for itself can
    never leak back into this process.
    """
    result = subprocess.run(
        ["alembic", "upgrade", "head"],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        msg = f"alembic upgrade head failed (exit {result.returncode}): {stderr}"
        raise RuntimeError(msg)


def job_downsync() -> None:
    # Belt-and-suspenders: this job is only ever registered in
    # non-production (see app/worker.py's build_cron_jobs()), but a
    # future registration bug must never let it run against a real
    # production database.
    if get_settings().app_environment == "production":
        logger.error("Downsync job invoked in production — refusing to run.")
        return

    local_storage = get_storage()
    started = datetime.now(UTC)

    try:
        prod_storage = build_prod_storage()
    except RuntimeError as exc:
        logger.exception("Downsync failed: could not load prod AWS credentials.")
        record_job_run(
            "downsync",
            started,
            exit_code=1,
            output=f"Could not load prod AWS credentials: {exc}",
        )
        return

    try:
        result = mirror_prefix(prod_storage, local_storage)
    except Exception as exc:
        logger.exception("Downsync S3 mirror failed.")
        record_job_run(
            "downsync", started, exit_code=1, output=f"S3 mirror failed: {exc}"
        )
        return

    if result.has_errors:
        logger.error(
            "Downsync S3 mirror had %d error(s), skipping DB restore.",
            len(result.errors),
        )
        record_job_run(
            "downsync",
            started,
            exit_code=1,
            output=f"S3 mirror had {len(result.errors)} error(s), restore skipped.",
        )
        return
    logger.info(
        "Downsync S3 mirror: %d synced, %d skipped, %d deleted.",
        len(result.synced),
        result.skipped,
        len(result.deleted),
    )

    try:
        restored_backup_name = run_restore(local_storage)
        _run_alembic_upgrade_head()
    except Exception as exc:
        logger.exception("Downsync DB restore/migration failed.")
        record_job_run(
            "downsync",
            started,
            exit_code=1,
            output=f"DB restore/migration failed: {exc}",
        )
        return

    logger.info("Downsync complete: local DB restored from %s.", restored_backup_name)
    record_job_run(
        "downsync",
        started,
        exit_code=0,
        output=(
            f"S3-Files: {len(result.synced)} synced, {result.skipped} skipped, "
            f"{len(result.deleted)} deleted; DB: restored from "
            f"{restored_backup_name}."
        ),
    )
