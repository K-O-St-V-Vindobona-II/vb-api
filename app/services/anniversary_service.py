"""Weekly member-anniversary computation for the "Verbindungschroniken" mail.

Shared by the standesdb_chronicles scheduler job (app/core/scheduler.py) and
the manual scripts/trigger_chronicles.py CLI, both parametrized by an
explicit `given` reference date instead of `datetime.now(UTC)` so the CLI
can replay any past or future week for testing.
"""

from datetime import date, timedelta
from typing import Literal, TypedDict

from sqlalchemy.orm import Session

from app.models.member import Member

AnniversaryStatus = Literal["lebend", "verstorben"]
AnniversaryField = Literal[
    "geburtsdatum", "aufnahmedatum", "burschungsdatum", "philistrierungsdatum"
]

ANNIVERSARY_FIELDS: tuple[AnniversaryField, ...] = (
    "geburtsdatum",
    "aufnahmedatum",
    "burschungsdatum",
    "philistrierungsdatum",
)

_LEAP_DAY_NOTE = "Jahrestag fällt eigentlich auf den 29. Februar"


class AnniversaryEntry(TypedDict):
    cn: str
    date: str
    years: int
    days_to: int
    leap_day_note: str | None


AnniversaryResult = dict[
    str, dict[AnniversaryStatus, dict[AnniversaryField, list[AnniversaryEntry]]]
]


def format_date_de(d: date) -> str:
    return f"{d.day}. {d.month}. {d.year}"


def week_window(given: date) -> tuple[date, date]:
    """Return the (Monday, Sunday) of the week following `given`'s week."""
    day_of_week = given.isoweekday()
    week_start = given + timedelta(days=(8 - day_of_week) % 7)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def get_opted_in_recipients(db: Session) -> list[str]:
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
    return [r[0] for r in recipients]


def _build_target_doys(week_start: date, week_end: date) -> set[int]:
    target_doys: set[int] = set()
    current = week_start
    while current <= week_end:
        target_doys.add(current.timetuple().tm_yday)
        current += timedelta(days=1)
    return target_doys


def _safe_anniversary_date(year: int, month: int, day: int) -> tuple[date, bool] | None:
    """Build `date(year, month, day)`, falling back to Feb 28 for a Feb 29
    anniversary in a non-leap year (Austrian/European civil convention)
    instead of silently dropping the anniversary. Returns
    (resolved_date, was_leap_day_shifted), or None if truly invalid."""
    try:
        return date(year, month, day), False
    except ValueError:
        if month == 2 and day == 29:
            return date(year, 2, 28), True
        return None


def _match_anniversary_date(
    ann_month: int,
    ann_day: int,
    given: date,
    target_doys: set[int],
) -> tuple[date, bool] | None:
    this_year = _safe_anniversary_date(given.year, ann_month, ann_day)
    if this_year and this_year[0].timetuple().tm_yday in target_doys:
        return this_year

    next_year = _safe_anniversary_date(given.year + 1, ann_month, ann_day)
    if next_year and next_year[0].timetuple().tm_yday in target_doys:
        return next_year
    return None


def _collect_field_anniversaries(
    db: Session,
    field: AnniversaryField,
    given: date,
    target_doys: set[int],
    result: AnniversaryResult,
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
        value: date | None = getattr(m, field)
        if value is None:
            continue

        match = _match_anniversary_date(value.month, value.day, given, target_doys)
        if not match:
            continue
        next_date, leap_shifted = match

        org = m.org_id or "vbw"
        status: AnniversaryStatus = "verstorben" if m.verstorben else "lebend"
        entry: AnniversaryEntry = {
            "cn": m.cn,
            "date": format_date_de(next_date),
            "years": next_date.year - value.year,
            "days_to": (next_date - given).days,
            "leap_day_note": _LEAP_DAY_NOTE if leap_shifted else None,
        }

        result.setdefault(org, {}).setdefault(status, {}).setdefault(field, [])
        result[org][status][field].append(entry)


def _days_to_key(entry: AnniversaryEntry) -> int:
    return entry["days_to"]


def _sort_anniversaries(result: AnniversaryResult) -> None:
    for org in result.values():
        for status in org.values():
            for entries in status.values():
                entries.sort(key=_days_to_key)


def compute_anniversaries(db: Session, given: date) -> AnniversaryResult:
    week_start, week_end = week_window(given)
    target_doys = _build_target_doys(week_start, week_end)

    result: AnniversaryResult = {}
    for field in ANNIVERSARY_FIELDS:
        _collect_field_anniversaries(db, field, given, target_doys, result)

    _sort_anniversaries(result)
    return result
