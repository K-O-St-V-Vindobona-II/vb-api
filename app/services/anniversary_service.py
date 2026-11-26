"""Weekly member-anniversary computation for the "Verbindungschroniken" mail.

Shared by the standesdb_chronicles scheduler job (app/core/scheduler.py) and
the manual scripts/trigger_chronicles.py CLI, both parametrized by an
explicit `given` reference date instead of `datetime.now(UTC)` so the CLI
can replay any past or future week for testing.
"""

from datetime import date, timedelta
from typing import TYPE_CHECKING, Literal, TypedDict

from app.core.datetime_utils import MONTHS_DE, WEEKDAYS_SHORT_DE, format_date_long
from app.core.mailer import render_template
from app.models.member import Member

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

AnniversaryField = Literal[
    "geburtsdatum",
    "aufnahmedatum",
    "burschungsdatum",
    "philistrierungsdatum",
    "sterbedatum",
]

# Also the order of the sections in the mail.
ANNIVERSARY_FIELDS: tuple[AnniversaryField, ...] = (
    "geburtsdatum",
    "aufnahmedatum",
    "burschungsdatum",
    "philistrierungsdatum",
    "sterbedatum",
)

_LEAP_DAY_NOTE = "Jahrestag fällt eigentlich auf den 29. Februar"


class AnniversaryEntry(TypedDict):
    cn: str
    deceased: bool
    date: date
    weekday: str
    day: str
    years: int
    leap_day_note: str | None


AnniversaryResult = dict[str, dict[AnniversaryField, list[AnniversaryEntry]]]


def week_window(given: date) -> tuple[date, date]:
    """Return the (Monday, Sunday) of the week following `given`'s week."""
    day_of_week = given.isoweekday()
    week_start = given + timedelta(days=(8 - day_of_week) % 7)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def period_label(week_start: date, week_end: date) -> str:
    """The week as the mail names it, e.g. "22. Februar bis 28. Februar 2027";
    the year is written twice only when the week spans New Year."""
    if week_start.year == week_end.year:
        start = f"{week_start.day}. {MONTHS_DE[week_start.month]}"
    else:
        start = format_date_long(week_start)
    return f"{start} bis {format_date_long(week_end)}"


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


def _members_with_exact(db: Session, field: AnniversaryField) -> list[Member]:
    """Members whose date of this kind is known to the day. Dismissed members
    never appear; a death date only counts for a member marked as deceased,
    as one on anybody else contradicts the record."""
    col = getattr(Member, field)
    acc_col = getattr(Member, f"{field}_accuracy")
    query = db.query(Member).filter(
        col.isnot(None),
        acc_col >= 3,
        Member.entlassen == False,  # noqa: E712
    )
    if field == "sterbedatum":
        query = query.filter(Member.verstorben == True)  # noqa: E712
    return query.all()


def _entry(
    member: Member, value: date, window: tuple[date, date]
) -> AnniversaryEntry | None:
    """The anniversary of `value` in the window, or None. A date of the
    window's own year (an event entered ahead of time) has no anniversary."""
    match = _anniversary_in_window(value, *window)
    if not match:
        return None
    anniversary, leap_shifted = match
    years = anniversary.year - value.year
    if years < 1:
        return None
    return {
        "cn": member.cn,
        "deceased": member.verstorben,
        "date": anniversary,
        "weekday": WEEKDAYS_SHORT_DE[anniversary.weekday()],
        "day": f"{anniversary.day}.{anniversary.month}.",
        "years": years,
        "leap_day_note": _LEAP_DAY_NOTE if leap_shifted else None,
    }


def _collect_field_anniversaries(
    db: Session,
    field: AnniversaryField,
    window: tuple[date, date],
    result: AnniversaryResult,
) -> None:
    for member in _members_with_exact(db, field):
        value: date | None = getattr(member, field)
        entry = _entry(member, value, window) if value else None
        if entry is None:
            continue
        org = member.org_id or "vbw"
        result.setdefault(org, {}).setdefault(field, []).append(entry)


def _sort_key(entry: AnniversaryEntry) -> tuple[date, str]:
    return entry["date"], entry["cn"]


def compute_anniversaries(db: Session, given: date) -> AnniversaryResult:
    """org -> kind of anniversary -> entries, by date and then by name."""
    window = week_window(given)

    result: AnniversaryResult = {}
    for field in ANNIVERSARY_FIELDS:
        _collect_field_anniversaries(db, field, window, result)

    for fields in result.values():
        for entries in fields.values():
            entries.sort(key=_sort_key)

    return result


def render_chronicles(anniversaries: AnniversaryResult, given: date) -> str:
    """The chronicle mail for the week after `given`; a week without any
    anniversary still gets a short mail, so the readers know it ran."""
    return render_template(
        "chronicles.html",
        anniversaries=anniversaries,
        fields=ANNIVERSARY_FIELDS,
        period=period_label(*week_window(given)),
    )
