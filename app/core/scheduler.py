"""Background jobs run by the process-wide APScheduler instance.

Every job function below catches a broad `Exception` around its body by
design: jobs run unattended, and one job's failure must never take down
the shared scheduler thread or block the other jobs from firing on their
own schedule. Each catch logs via `logger.exception(...)` (not
`logger.warning`), which Ruff's BLE001 specifically exempts from the
blind-except check since it preserves the full traceback rather than
silently swallowing it.
"""

import logging
import subprocess
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection
    from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.mailer import render_template, send_to_recipients
from app.core.security import (
    REFRESH_TOKEN_LIFETIME_DAYS,
    SESSION_IDLE_TIMEOUT_MINUTES,
)
from app.core.storage import get_storage
from app.core.tasks import TRACKING_RETENTION_MONTHS
from app.db.database import SessionLocal, engine
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
from app.services.downsync_service import build_prod_storage, load_aws_env
from app.services.p4x_category_service import apply_all_category_filters
from app.services.p4x_fee_balance_service import calculate_fee_balance, fee_for_month
from app.services.permission_service import get_emails_with_permission
from app.services.s3_mirror_service import mirror_prefix
from app.services.scheduled_task_run_service import record_job_run
from app.services.storage_integrity_service import (
    check_archive_integrity,
    check_standesdb_integrity,
)

BACKUP_ENABLED: bool = get_settings().backup_enabled
BACKUP_HOUR: int = get_settings().backup_hour
DOWNSYNC_HOUR: int = (BACKUP_HOUR + 1) % 24

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

APP_TZ = ZoneInfo(get_settings().app_timezone)

# All cron trigger hour/minute values below are wall-clock time in the
# configured app timezone (Settings.app_timezone, default Europe/Vienna;
# human-facing mails), not UTC — the machine-facing db_backup job further
# below stays UTC-based and is documented as such.
scheduler = AsyncIOScheduler(timezone=APP_TZ)

# Arbitrary fixed key identifying "the vb-api scheduler" for
# pg_try_advisory_lock. Any int64 works as long as it stays constant.
_SCHEDULER_LOCK_KEY = 8_872_501

# Held open for the process lifetime once acquired, so the advisory lock
# stays taken until this worker process exits (Postgres releases advisory
# locks automatically when the holding connection closes).
_scheduler_lock_conn: "Connection | None" = None


def _acquire_scheduler_lock() -> bool:
    """Ensure only one process runs the scheduler.

    Production runs vb-api as multiple gunicorn worker processes, each with
    its own in-memory AsyncIOScheduler. Without this lock, every worker
    registers and fires the same cron jobs independently, so each scheduled
    job (health-check mails, chronicles, backups, ...) runs once per worker
    instead of once per deployment. SQLite (dev-only fallback) never runs
    with multiple workers, so it skips the lock and always starts.
    """
    global _scheduler_lock_conn  # noqa: PLW0603 -- lazy singleton, holds the advisory-lock connection open for process lifetime

    if engine.dialect.name != "postgresql":
        return True

    conn = engine.connect()
    acquired = bool(
        conn.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": _SCHEDULER_LOCK_KEY},
        ).scalar()
    )
    if not acquired:
        conn.close()
        return False

    _scheduler_lock_conn = conn
    return True


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
        tomorrow = datetime.now(UTC).date() + timedelta(days=1)

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
                from_addr="philchc@mg.vindobona2.at",
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
    db: "Session",
    role_ids: list[str],
    org_id: str,
) -> list[str]:
    today = datetime.now(UTC).date()
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


def _validate_latest_booking(db: "Session", today: date) -> bool:
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
    db: "Session",
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
            from_addr="philisterkassier@mg.vindobona2.at",
            from_name=sender_name,
            reply_to=sender_email,
            bcc_emails=bcc_emails,
        )
        logger.info("Debtor reminder sent to %s (debt: %d)", m.cn, debt)


def job_debtor_reminder() -> None:
    today = datetime.now(UTC).date()
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


def _get_phil_xxxx_name(db: "Session") -> str:
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
    db: "Session",
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

        given = datetime.now(UTC).date()
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


async def job_db_backup() -> None:
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
    # non-production (see start_scheduler()), but a future registration
    # bug must never let it run against a real production database.
    if get_settings().app_environment == "production":
        logger.error("Downsync job invoked in production — refusing to run.")
        return

    local_storage = get_storage()
    started = datetime.now(UTC)

    try:
        aws_env = load_aws_env()
        prod_storage = build_prod_storage(aws_env)
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


# -------------------------------------------------------------------
# Register all jobs
# -------------------------------------------------------------------

JOB_DESCRIPTIONS: dict[str, str] = {
    "cleanup": (
        "Bereinigt abgelaufene Sessions,"
        " Password-Reset-Tokens, alte"
        " Aktivitätsprotokolle und versandte"
        " Emails sowie verwaiste User-Agents."
    ),
    "refresh_category_filter_hits": (
        "Berechnet die Treffer aller"
        " Kategorie-Filter in den AH-Kassen"
        " neu. Bereits direkt zugeordnete"
        " Transaktionen werden übersprungen."
    ),
    "birthday_mails": (
        "Sendet Geburtstagsgrüße an"
        " VBW-Mitglieder, die morgen"
        " Geburtstag haben. BCC an den"
        " Philister-ChC."
    ),
    "debtor_reminder": (
        "Sendet vierteljährlich Erinnerungen"
        " an Mitglieder mit einem"
        " Beitragsrückstand von über 300 Euro."
        " Enthält IBAN, BIC und aktuelle"
        " Beitragshöhe."
    ),
    "standesdb_chronicles": (
        "Versendet die wöchentliche"
        " Jubiläums-Chronik (Geburtstage,"
        " Aufnahmen, Burschungen,"
        " Philistrierungen) an alle"
        " Mitglieder, die den Versand"
        " aktiviert haben."
    ),
    "archive_health_check": (
        "Prüft wöchentlich, ob alle im Archiv"
        " referenzierten Dateien in S3 vorhanden"
        " sind, meldet verwaiste S3-Objekte und"
        " unsortierte Uploads. Versendet einen"
        " Bericht an alle Mitglieder mit der"
        " Berechtigung 'archiveAdmin'."
    ),
    "standesdb_health_check": (
        "Prüft wöchentlich, ob alle in der"
        " Standesdatenbank referenzierten Bilder"
        " in S3 vorhanden sind, und meldet"
        " verwaiste S3-Objekte. Versendet einen"
        " Bericht an alle Mitglieder mit der"
        " Berechtigung 'standesdbVbwAdmin'."
    ),
    "db_backup": (
        "Erstellt täglich um BACKUP_HOUR Uhr"
        " (Default 03:00 UTC) eine vollständige"
        " PostgreSQL-Sicherung und lädt sie auf S3 hoch."
        " Dateiname: [environment]-YYYY-MM-DD_HH-MM-SS.dump."
        " Löscht anschließend Backups, die älter als"
        " BACKUP_RETENTION_DAYS (Default 29) sind."
    ),
    "downsync": (
        "Nur auf Non-Production-Stages: spiegelt einmal täglich"
        " (DOWNSYNC_HOUR, Default 04:00 UTC, eine Stunde nach dem"
        " Prod-Backup) den kompletten Produktions-S3-Bucket auf die"
        " Storage dieser Stage und restored anschließend das gerade"
        " gespiegelte, frischeste Backup lokal (inkl."
        " 'alembic upgrade head'). Sorgt dafür, dass Non-Production"
        " einmal täglich mit aktuellen Produktivdaten versorgt wird."
    ),
}


def _register_production_jobs() -> None:
    scheduler.add_job(
        job_refresh_category_filter_hits,
        "cron",
        hour=7,
        minute=0,
        id="refresh_category_filter_hits",
        replace_existing=True,
    )
    scheduler.add_job(
        job_birthday_mails,
        "cron",
        hour=15,
        minute=53,
        id="birthday_mails",
        replace_existing=True,
    )
    scheduler.add_job(
        job_debtor_reminder,
        "cron",
        day=25,
        hour=18,
        minute=32,
        id="debtor_reminder",
        replace_existing=True,
    )
    scheduler.add_job(
        job_standesdb_chronicles,
        "cron",
        day_of_week="tue",
        hour=17,
        minute=0,
        id="standesdb_chronicles",
        replace_existing=True,
    )
    scheduler.add_job(
        job_archive_health_check,
        "cron",
        day_of_week="tue",
        hour=1,
        minute=0,
        id="archive_health_check",
        replace_existing=True,
    )
    scheduler.add_job(
        job_standesdb_health_check,
        "cron",
        day_of_week="tue",
        hour=3,
        minute=0,
        id="standesdb_health_check",
        replace_existing=True,
    )
    if BACKUP_ENABLED:
        scheduler.add_job(
            job_db_backup,
            "cron",
            hour=BACKUP_HOUR,
            minute=0,
            timezone=UTC,
            id="db_backup",
            replace_existing=True,
        )
    else:
        logger.info("DB backup job disabled via BACKUP_ENABLED=false.")


def _register_non_production_jobs() -> None:
    scheduler.add_job(
        job_downsync,
        "cron",
        hour=DOWNSYNC_HOUR,
        minute=0,
        timezone=UTC,
        id="downsync",
        replace_existing=True,
    )


def start_scheduler() -> None:
    # Every worker registers the same jobs, lock or no lock — this is what
    # makes get_scheduled_jobs() below return identical results regardless
    # of which of the (production: 2) gunicorn workers handles the request.
    # Registering a job on a scheduler that never starts is inert: nothing
    # fires, only the trigger's schedule becomes introspectable.
    scheduler.add_job(
        job_cleanup,
        "cron",
        minute=0,
        id="cleanup",
        replace_existing=True,
    )

    # Read fresh (not a module-level constant like BACKUP_ENABLED/BACKUP_HOUR
    # above) so tests can flip APP_ENVIRONMENT per test case and observe
    # different registration behavior from the same running process.
    if get_settings().app_environment == "production":
        _register_production_jobs()
    else:
        _register_non_production_jobs()

    if not _acquire_scheduler_lock():
        logger.info(
            "Scheduler lock held by another worker process — jobs are"
            " registered for introspection, but execution stays disabled"
            " in this process."
        )
        return

    scheduler.start()
    logger.info(
        "Scheduler started with %d jobs.",
        len(scheduler.get_jobs()),
    )


def _format_next_run(job: Job, now: datetime) -> str | None:
    """Compute a job's next fire time fresh from its trigger, not its cache.

    Job.next_run_time only keeps advancing while the scheduler that added
    it is actually ticking (scheduler.start()) — in production that is
    exactly one of the two gunicorn workers (see _acquire_scheduler_lock).
    The other worker's copy of the same job freezes at whatever
    next_run_time was computed at add_job() time on process startup, which
    goes stale (shows a past date) once that moment passes. Deriving it
    from the trigger and the current time instead keeps every worker's
    answer correct and identical.
    """
    next_run = job.trigger.get_next_fire_time(None, now)
    if next_run is None:
        return None
    return next_run.strftime("%d.%m.%Y, %H:%M")


def get_scheduled_jobs() -> list[dict[str, str | None]]:
    now = datetime.now(APP_TZ)
    return [
        {
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run": _format_next_run(job, now),
            "description": JOB_DESCRIPTIONS.get(job.id),
        }
        for job in scheduler.get_jobs()
    ]


def stop_scheduler() -> None:
    global _scheduler_lock_conn  # noqa: PLW0603 -- releases the singleton set up in ensure_single_scheduler_instance()

    if scheduler.running:
        scheduler.shutdown(wait=False)

    if _scheduler_lock_conn is not None:
        _scheduler_lock_conn.close()
        _scheduler_lock_conn = None

    logger.info("Scheduler stopped.")
