import logging
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from app.core.mailer import render_template, send_to_recipients
from app.core.security import (
    REFRESH_TOKEN_LIFETIME_DAYS,
    SESSION_IDLE_TIMEOUT_MINUTES,
)
from app.core.tasks import TRACKING_RETENTION_MONTHS
from app.db.database import SessionLocal
from app.models.client_user_agent import ClientUserAgent
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.p4x_transaction import P4xTransaction
from app.models.password_reset import PasswordResetToken
from app.models.personal_access_token import PersonalAccessToken
from app.models.request_log import RequestLog
from app.models.sent_email import SentEmail
from app.services.p4x_service import (
    apply_all_category_filters,
    calculate_fee_balance,
    fee_for_month,
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

scheduler = AsyncIOScheduler()


def _format_date_de(d: date) -> str:
    return f"{d.day}. {d.month}. {d.year}"


# -------------------------------------------------------------------
# Cleanup: expired tokens, old logs — hourly
# -------------------------------------------------------------------


def job_cleanup() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(UTC)

        max_lifetime = now - timedelta(
            days=REFRESH_TOKEN_LIFETIME_DAYS,
        )
        db.query(PersonalAccessToken).filter(
            PersonalAccessToken.created_at < max_lifetime,
        ).delete()

        idle = now - timedelta(
            minutes=SESSION_IDLE_TIMEOUT_MINUTES,
        )
        db.query(PersonalAccessToken).filter(
            PersonalAccessToken.last_used_at < idle,
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
    except Exception:
        logger.exception("Cleanup failed")
    finally:
        db.close()


# -------------------------------------------------------------------
# Task 1: RefreshCategoryFilterHits — daily 07:00
# -------------------------------------------------------------------


def job_refresh_category_filter_hits() -> None:
    db = SessionLocal()
    try:
        apply_all_category_filters(db, truncate_first=True)
        logger.info("Category filter hits refreshed.")
    except Exception:
        logger.exception("RefreshCategoryFilterHits failed")
    finally:
        db.close()


# -------------------------------------------------------------------
# Task 2: BirthdayMails — daily 15:53
# -------------------------------------------------------------------


def job_birthday_mails() -> None:
    db = SessionLocal()
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
                reply_to="philchc@vindobona2.at",
                bcc_emails=bcc_emails,
            )
            logger.info(
                "Birthday mail sent to %s",
                m.cn,
            )
    except Exception:
        logger.exception("BirthdayMails failed")
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
            reply_to=sender_email,
            bcc_emails=bcc_emails,
        )
        logger.info("Debtor reminder sent to %s (debt: %d)", m.cn, debt)


def job_debtor_reminder() -> None:
    today = datetime.now(UTC).date()
    if today.month % 3 == 0:
        return

    db = SessionLocal()
    try:
        if not _validate_latest_booking(db, today):
            return

        target = _compute_target_date(today)
        target_str = target.strftime("%Y-%m-%d")
        _send_debtor_reminders(db, target, target_str)
    except Exception:
        logger.exception("DebtorReminder failed")
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
    try:
        recipients = (
            db.query(Member.email)
            .filter(
                Member.entlassen == False,  # noqa: E712
                Member.verstorben == False,  # noqa: E712
                Member.chroniclemail == True,  # noqa: E712
                Member.email.isnot(None),
                Member.email != "",
            )
            .all()
        )
        to_emails = [r[0] for r in recipients]
        if not to_emails:
            return

        anniversaries = _compute_anniversaries(db)
        if not anniversaries:
            return

        today = datetime.now(UTC).date()
        day_of_week = today.isoweekday()
        week_start = today + timedelta(
            days=(8 - day_of_week) % 7,
        )
        week_end = week_start + timedelta(days=6)

        html = render_template(
            "chronicles.html",
            anniversaries=anniversaries,
            start=_format_date_de(week_start),
            end=_format_date_de(week_end),
        )
        send_to_recipients(
            to_emails=to_emails,
            subject="Verbindungschroniken",
            html_content=html,
            template_key="chronicles",
        )
        logger.info(
            "Chronicles sent to %d recipients.",
            len(to_emails),
        )
    except Exception:
        logger.exception("Chronicles failed")
    finally:
        db.close()


def _build_target_doys(week_start: date, week_end: date) -> set[int]:
    target_doys: set[int] = set()
    current = week_start
    while current <= week_end:
        target_doys.add(current.timetuple().tm_yday)
        current += timedelta(days=1)
    return target_doys


def _match_anniversary_date(
    ann_month: int,
    ann_day: int,
    today: date,
    target_doys: set[int],
) -> date | None:
    try:
        ann_this_year = date(today.year, ann_month, ann_day)
    except ValueError:
        return None

    if ann_this_year.timetuple().tm_yday in target_doys:
        return ann_this_year

    try:
        ann_next_year = date(today.year + 1, ann_month, ann_day)
    except ValueError:
        return None

    if ann_next_year.timetuple().tm_yday in target_doys:
        return ann_next_year
    return None


def _collect_field_anniversaries(
    db: "Session",
    field: str,
    today: date,
    target_doys: set[int],
    result: dict[str, dict[str, dict[str, list[dict[str, object]]]]],
) -> None:
    col = getattr(Member, field)
    acc_col = getattr(Member, f"{field}_accuracy")

    members = (
        db.query(Member)
        .filter(
            col.isnot(None),
            acc_col >= 3,
            Member.entlassen == False,  # noqa: E712
        )
        .all()
    )

    for m in members:
        raw = str(getattr(m, field))
        parts = raw.split("-")
        if len(parts) < 3:
            continue

        next_date = _match_anniversary_date(
            int(parts[1]), int(parts[2]), today, target_doys
        )
        if not next_date:
            continue

        years = next_date.year - int(parts[0])
        org = m.org_id or "vbw"
        status = "verstorben" if m.verstorben else "lebend"

        result.setdefault(org, {}).setdefault(status, {}).setdefault(field, [])
        result[org][status][field].append(
            {
                "cn": m.cn,
                "date": _format_date_de(next_date),
                "years": years,
                "days_to": (next_date - today).days,
            }
        )


def _days_to_key(entry: dict[str, object]) -> int:
    val = entry.get("days_to", 0)
    return val if isinstance(val, int) else 0


def _sort_anniversaries(
    result: dict[str, dict[str, dict[str, list[dict[str, object]]]]],
) -> None:
    for org in result.values():
        for status in org.values():
            for entries in status.values():
                entries.sort(key=_days_to_key)


def _compute_anniversaries(
    db: "Session",
) -> dict[str, dict[str, dict[str, list[dict[str, object]]]]]:
    today = datetime.now(UTC).date()
    day_of_week = today.isoweekday()
    week_start = today + timedelta(days=(8 - day_of_week) % 7)
    week_end = week_start + timedelta(days=6)

    target_doys = _build_target_doys(week_start, week_end)

    ann_fields = [
        "geburtsdatum",
        "aufnahmedatum",
        "burschungsdatum",
        "philistrierungsdatum",
    ]

    result: dict[str, dict[str, dict[str, list[dict[str, object]]]]] = {}
    for field in ann_fields:
        _collect_field_anniversaries(db, field, today, target_doys, result)

    _sort_anniversaries(result)
    return result


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
}


def start_scheduler() -> None:
    scheduler.add_job(
        job_cleanup,
        "interval",
        hours=1,
        id="cleanup",
        replace_existing=True,
    )
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
    scheduler.start()
    logger.info(
        "Scheduler started with %d jobs.",
        len(scheduler.get_jobs()),
    )


def get_scheduled_jobs() -> list[dict[str, str | None]]:
    return [
        {
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run": (
                job.next_run_time.strftime(
                    "%d.%m.%Y, %H:%M",
                )
                if job.next_run_time
                else None
            ),
            "description": JOB_DESCRIPTIONS.get(job.id),
        }
        for job in scheduler.get_jobs()
    ]


def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped.")
