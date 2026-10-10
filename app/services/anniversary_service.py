"""Weekly member-anniversary computation for the "Verbindungschroniken" mail.

Shared by the standesdb_chronicles scheduler job (app/core/scheduler.py) and
the manual scripts/trigger_chronicles.py CLI, both parametrized by an
explicit `given` reference date instead of `datetime.now(UTC)` so the CLI
can replay any past or future week for testing.
"""

from datetime import date, timedelta
from typing import TYPE_CHECKING, Literal, TypedDict

from app.models.member import Member

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

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


def _anniversary_in_window(
    value: date, week_start: date, week_end: date
) -> tuple[date, bool] | None:
    """The anniversary of `value` that falls inside [week_start, week_end],
    as (date, was_leap_day_shifted), or None. A week can span New Year, so
    both calendar years of the window are tried; comparing real dates (not
    day-of-year numbers) keeps a January anniversary in a late-December
    window in the following year instead of the year that already passed.
    """
    for year in sorted({week_start.year, week_end.year}):
        candidate = _safe_anniversary_date(year, value.month, value.day)
        if candidate and week_start <= candidate[0] <= week_end:
            return candidate
    return None


def _collect_field_anniversaries(
    db: Session,
    field: AnniversaryField,
    given: date,
    window: tuple[date, date],
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

        match = _anniversary_in_window(value, *window)
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


def compute_anniversaries(db: Session, given: date) -> AnniversaryResult:
    window = week_window(given)

    result: AnniversaryResult = {}
    for field in ANNIVERSARY_FIELDS:
        _collect_field_anniversaries(db, field, given, window, result)

    for org in result.values():
        for status in org.values():
            for entries in status.values():
                entries.sort(key=_days_to_key)

    return result
