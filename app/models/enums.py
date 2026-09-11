"""Python-side StrEnum classes backing native Postgres ENUM columns.

Each class here is the single source of truth for a bounded status/category
field — used both as the SQLAlchemy column type (via sqlalchemy.Enum) and
as the Pydantic field type in app/schemas/*.py, replacing what used to be
duplicated as a separate hand-written validator in each schema.
"""

from enum import StrEnum


def enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """values_callable for sqlalchemy.Enum — without this, SQLAlchemy stores
    the Python member NAME (e.g. "FUNKTION") instead of its value
    ("funktion"), which doesn't match the lowercase labels the Postgres
    ENUM type was created with."""
    return [member.value for member in enum_cls]


class ContactType(StrEnum):
    PERSON = "person"
    ORGANISATION = "organisation"


class BadgeGroup(StrEnum):
    JUBELBAND = "jubelband"
    EHRENZEICHEN = "ehrenzeichen"


class RoleGroup(StrEnum):
    PHILCHC = "philchc"
    FUNKTION = "funktion"
    VERBINDUNGSGERICHT = "verbindungsgericht"
    KOMMISSION = "kommission"
    CHC = "chc"


class SubjectMode(StrEnum):
    CONTAINS = "contains"
    EQUALS = "equals"
    STARTS = "starts"


class MemberDeliveryPreference(StrEnum):
    DEAKTIVIERT = "deaktiviert"
    ADRESSE_PRIVAT = "adresse_privat"
    ADRESSE_BERUF = "adresse_beruf"


class ChangeLogAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class MemberChangeRequestStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"


class AboutTabSlot(StrEnum):
    ANFANG = "anfang"
    MKV = "mkv"
    HEUTE = "heute"


class JobId(StrEnum):
    """Every id registered in app.core.job_schedule_registry.JOB_REGISTRY.

    Single source of truth for a scheduled job's identity across the web
    and worker containers — the Postgres ENUM column, the cron schedule
    registry, and the ARQ task dispatch table all bind to this class
    instead of duplicating the raw strings independently."""

    CLEANUP = "cleanup"
    REFRESH_CATEGORY_FILTER_HITS = "refresh_category_filter_hits"
    BIRTHDAY_MAILS = "birthday_mails"
    DEBTOR_REMINDER = "debtor_reminder"
    STANDESDB_CHRONICLES = "standesdb_chronicles"
    ARCHIVE_HEALTH_CHECK = "archive_health_check"
    STANDESDB_HEALTH_CHECK = "standesdb_health_check"
    DB_BACKUP = "db_backup"
    DOWNSYNC = "downsync"
