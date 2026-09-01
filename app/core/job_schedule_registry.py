"""Declarative schedule metadata for every scheduled (cron) job.

Single source of truth for "when does each job run" and "which stage
runs it" — imported by both the ARQ worker container (builds its real
`arq.cron.cron(...)` entries from this data, see app/worker.py) and the
web container (computes read-only "next run" introspection for
GET /api/system/scheduled-jobs from the exact same data, via arq's own
next_cron(), with no live scheduler/worker instance needed at all). Both
containers filtering through applicable_entries() is what keeps them from
ever disagreeing on what's scheduled where.

Each cron field (day/weekday/hour/minute) is its own explicitly typed
attribute on JobScheduleEntry, passed to next_cron()/cron() as a named
keyword argument at each call site — not collected into a generic dict
and **-unpacked, which Pyright cannot verify precisely against arq's many
keyword-only parameters (it can't tell which dict key maps to which
parameter, so it checks the value type against every parameter).
`weekday` uses arq's own weekday spelling (`arq.typing.WEEKDAYS`:
'mon'/'tues'/'wed'/'thurs'/'fri'/'sat'/'sun') — notably "tues", not
APScheduler's former "tue".
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from arq.cron import next_cron

from app.core.config import get_settings
from app.core.datetime_utils import get_app_timezone

if TYPE_CHECKING:
    from arq.typing import WeekdayOptionType

# Frozen at import time, exactly like the pre-existing scheduler.py did —
# only APP_ENVIRONMENT itself needs to be read fresh (see
# applicable_entries()), so tests can flip it per test case.
BACKUP_HOUR: int = get_settings().backup_hour
DOWNSYNC_HOUR: int = (BACKUP_HOUR + 1) % 24

JobScope = Literal["always", "production_only", "non_production_only"]


@dataclass(frozen=True)
class JobScheduleEntry:
    id: str
    description: str
    scope: JobScope
    day: int | None = None
    weekday: WeekdayOptionType = None
    hour: int | None = None
    minute: int = 0


JOB_REGISTRY: tuple[JobScheduleEntry, ...] = (
    JobScheduleEntry(
        id="cleanup",
        scope="always",
        description=(
            "Bereinigt abgelaufene Sessions,"
            " Password-Reset-Tokens, alte"
            " Aktivitätsprotokolle und versandte"
            " Emails sowie verwaiste User-Agents."
        ),
    ),
    JobScheduleEntry(
        id="refresh_category_filter_hits",
        hour=7,
        scope="production_only",
        description=(
            "Berechnet die Treffer aller"
            " Kategorie-Filter in den AH-Kassen"
            " neu. Bereits direkt zugeordnete"
            " Transaktionen werden übersprungen."
        ),
    ),
    JobScheduleEntry(
        id="birthday_mails",
        hour=15,
        minute=53,
        scope="production_only",
        description=(
            "Sendet Geburtstagsgrüße an"
            " VBW-Mitglieder, die morgen"
            " Geburtstag haben. BCC an den"
            " Philister-ChC."
        ),
    ),
    JobScheduleEntry(
        id="debtor_reminder",
        day=25,
        hour=18,
        minute=32,
        scope="production_only",
        description=(
            "Sendet vierteljährlich Erinnerungen"
            " an Mitglieder mit einem"
            " Beitragsrückstand von über 300 Euro."
            " Enthält IBAN, BIC und aktuelle"
            " Beitragshöhe."
        ),
    ),
    JobScheduleEntry(
        id="standesdb_chronicles",
        weekday="tues",
        hour=17,
        scope="production_only",
        description=(
            "Versendet die wöchentliche"
            " Jubiläums-Chronik (Geburtstage,"
            " Aufnahmen, Burschungen,"
            " Philistrierungen) an alle"
            " Mitglieder, die den Versand"
            " aktiviert haben."
        ),
    ),
    JobScheduleEntry(
        id="archive_health_check",
        weekday="tues",
        hour=1,
        scope="production_only",
        description=(
            "Prüft wöchentlich, ob alle im Archiv"
            " referenzierten Dateien in S3 vorhanden"
            " sind, meldet verwaiste S3-Objekte und"
            " unsortierte Uploads. Versendet einen"
            " Bericht an alle Mitglieder mit der"
            " Berechtigung 'archiveAdmin'."
        ),
    ),
    JobScheduleEntry(
        id="standesdb_health_check",
        weekday="tues",
        hour=3,
        scope="production_only",
        description=(
            "Prüft wöchentlich, ob alle in der"
            " Standesdatenbank referenzierten Bilder"
            " in S3 vorhanden sind, und meldet"
            " verwaiste S3-Objekte. Versendet einen"
            " Bericht an alle Mitglieder mit der"
            " Berechtigung 'standesdbVbwAdmin'."
        ),
    ),
    JobScheduleEntry(
        id="db_backup",
        hour=BACKUP_HOUR,
        scope="production_only",
        description=(
            "Erstellt täglich um BACKUP_HOUR Uhr"
            " (Default 03:00, App-Zeitzone) eine vollständige"
            " PostgreSQL-Sicherung und lädt sie auf S3 hoch."
            " Dateiname: [environment]-YYYY-MM-DD_HH-MM-SS.dump."
            " Löscht anschließend Backups, die älter als"
            " BACKUP_RETENTION_DAYS (Default 29) sind."
        ),
    ),
    JobScheduleEntry(
        id="downsync",
        hour=DOWNSYNC_HOUR,
        scope="non_production_only",
        description=(
            "Nur auf Non-Production-Stages: spiegelt einmal täglich"
            " (DOWNSYNC_HOUR, Default 04:00, App-Zeitzone, eine Stunde nach dem"
            " Prod-Backup) den kompletten Produktions-S3-Bucket auf die"
            " Storage dieser Stage und restored anschließend das gerade"
            " gespiegelte, frischeste Backup lokal (inkl."
            " 'alembic upgrade head'). Sorgt dafür, dass Non-Production"
            " einmal täglich mit aktuellen Produktivdaten versorgt wird."
        ),
    ),
)


def _describe_trigger(entry: JobScheduleEntry) -> str:
    """Cosmetic only — matches APScheduler's canonical cron field order
    (month/day/weekday/hour/minute) and its `day_of_week` naming, purely
    for continuity with the trigger strings this endpoint returned before
    this migration. Nothing parses this format; the frontend just
    displays it as opaque text."""
    parts = []
    if entry.day is not None:
        parts.append(f"day='{entry.day}'")
    if entry.weekday is not None:
        parts.append(f"day_of_week='{entry.weekday}'")
    if entry.hour is not None:
        parts.append(f"hour='{entry.hour}'")
    parts.append(f"minute='{entry.minute}'")
    return f"cron[{', '.join(parts)}]"


def applicable_entries(
    *, app_environment: str, backup_enabled: bool
) -> list[JobScheduleEntry]:
    """Filter JOB_REGISTRY down to what actually applies to one stage.

    The one rule both the worker (registers real arq cron jobs) and the
    web container (introspection only) must apply identically.
    """
    is_production = app_environment == "production"
    result = []
    for entry in JOB_REGISTRY:
        if entry.scope == "production_only" and not is_production:
            continue
        if entry.scope == "non_production_only" and is_production:
            continue
        if entry.id == "db_backup" and not backup_enabled:
            continue
        result.append(entry)
    return result


def get_scheduled_jobs() -> list[dict[str, str | None]]:
    settings = get_settings()
    now = datetime.now(get_app_timezone())
    entries = applicable_entries(
        app_environment=settings.app_environment or "",
        backup_enabled=settings.backup_enabled,
    )
    result: list[dict[str, str | None]] = []
    for entry in entries:
        # next_cron() always returns a datetime (never None) — unlike
        # APScheduler's get_next_fire_time(), none of our entries have an
        # expiring end date/year, so it always converges.
        next_run = next_cron(
            now,
            day=entry.day,
            weekday=entry.weekday,
            hour=entry.hour,
            minute=entry.minute,
        )
        result.append(
            {
                "id": entry.id,
                "name": f"job_{entry.id}",
                "trigger": _describe_trigger(entry),
                "next_run": next_run.strftime("%d.%m.%Y, %H:%M"),
                "description": entry.description,
            }
        )
    return result
