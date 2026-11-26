"""Readable old and new values of a saved directory entry, for the mail that
tells the administrators about the change.

The raw diff of a save holds stored values: dates whose precision lives in a
separate `*_accuracy` field, and badges, keys and roles as lists of ids. This
module turns it into the text a reader expects, e.g. "Mai 1990" or
"Fuxenband (17. Mai 2020)", and leaves out what reads the same before and
after.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import TYPE_CHECKING, cast

from app.core.datetime_utils import format_date_long, format_fuzzy_date
from app.models.badge import Badge
from app.models.key import Key
from app.models.role import Role

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.core.mailer import FieldChange
    from app.models.contact import Contact
    from app.models.member import Member

# The fields of a member and a contact whose date has its own accuracy.
FUZZY_DATE_FIELDS = frozenset(
    {
        "geburtsdatum",
        "aufnahmedatum",
        "branderdatum",
        "burschungsdatum",
        "philistrierungsdatum",
        "entlassungsdatum",
        "sterbedatum",
        "datum",
    }
)
_ACCURACY_SUFFIX = "_accuracy"
_NOTHING = "-"

Entry = Mapping[str, object]


def _as_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value)
    return None


def _as_int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _plain(value: object) -> str:
    if value is None or value == "":
        return _NOTHING
    return str(value)


def _describe_date(
    field: str,
    diff: Mapping[str, Mapping[str, object]],
    entry: Member | Contact,
) -> FieldChange:
    """Old and new date, each at its own accuracy. An accuracy missing from
    the diff did not change, so the stored one holds for both sides."""
    unchanged = _as_int(getattr(entry, f"{field}{_ACCURACY_SUFFIX}", 0))
    accuracy = diff.get(
        f"{field}{_ACCURACY_SUFFIX}", {"old": unchanged, "new": unchanged}
    )
    values = diff.get(
        field, {"old": getattr(entry, field), "new": getattr(entry, field)}
    )
    return {
        "old": format_fuzzy_date(_as_date(values["old"]), _as_int(accuracy["old"])),
        "new": format_fuzzy_date(_as_date(values["new"]), _as_int(accuracy["new"])),
    }


def _ids(change: Mapping[str, object]) -> set[object]:
    return {
        entry["id"]
        for side in ("old", "new")
        for entry in cast("Sequence[Entry]", change.get(side) or [])
    }


def _badge_names(db: Session, ids: set[object]) -> dict[object, str]:
    rows = db.query(Badge.id, Badge.name).filter(Badge.id.in_(ids)).all()
    return {row_id: name or "" for row_id, name in rows}


def _key_names(db: Session, ids: set[object]) -> dict[object, str]:
    rows = db.query(Key.id, Key.name).filter(Key.id.in_(ids)).all()
    return {row_id: name or "" for row_id, name in rows}


def _role_labels(db: Session, ids: set[object]) -> dict[object, str]:
    rows = db.query(Role.id, Role.label).filter(Role.id.in_(ids)).all()
    return {row_id: label or "" for row_id, label in rows}


def _presented(entry: Entry, names: Mapping[object, str]) -> str:
    """A badge or key: its name, and the date it was presented when known."""
    name = names.get(entry["id"]) or str(entry["id"])
    accuracy = _as_int(entry.get("presentationdate_accuracy"))
    presented = _as_date(entry.get("presentationdate"))
    if presented is None or not accuracy:
        return name
    return f"{name} ({format_fuzzy_date(presented, accuracy)})"


def _held(entry: Entry, labels: Mapping[object, str]) -> str:
    """A role and the time it was held."""
    label = labels.get(entry["id"]) or str(entry["id"])
    # A role always has a start date (NOT NULL), only the end is open.
    start = format_date_long(cast("date", _as_date(entry["startdate"])))
    end = _as_date(entry.get("enddate"))
    if end is None:
        return f"{label} (seit {start})"
    return f"{label} ({start} bis {format_date_long(end)})"


def _describe_list(
    change: Mapping[str, object],
    describe_one: Callable[[Entry], str],
) -> FieldChange:
    def side(name: str) -> str:
        entries = cast("Sequence[Entry]", change.get(name) or [])
        return "; ".join(describe_one(e) for e in entries) or _NOTHING

    return {"old": side("old"), "new": side("new")}


_NAME_LOOKUPS: dict[str, Callable[[Session, set[object]], dict[object, str]]] = {
    "badges": _badge_names,
    "keys": _key_names,
    "roles_history": _role_labels,
}


def _describe_entries(
    db: Session, key: str, change: Mapping[str, object]
) -> FieldChange:
    names = _NAME_LOOKUPS[key](db, _ids(change))
    if key == "roles_history":
        return _describe_list(change, lambda e: _held(e, names))
    return _describe_list(change, lambda e: _presented(e, names))


def _describe_field(
    db: Session,
    key: str,
    diff: Mapping[str, Mapping[str, object]],
    entry: Member | Contact,
) -> FieldChange:
    if key in FUZZY_DATE_FIELDS:
        return _describe_date(key, diff, entry)
    if key in _NAME_LOOKUPS:
        return _describe_entries(db, key, diff[key])
    return {"old": _plain(diff[key]["old"]), "new": _plain(diff[key]["new"])}


def describe_changes(
    db: Session,
    diff: Mapping[str, Mapping[str, object]],
    entry: Member | Contact,
) -> dict[str, FieldChange]:
    """Readable old and new value of every changed field of the saved
    `entry`. A field that reads the same before and after (a day changed
    at year accuracy, say) is left out."""
    # A changed accuracy stands for its date field, even when the date itself
    # did not change; dict.fromkeys keeps the diff order without duplicates.
    keys = dict.fromkeys(k.removesuffix(_ACCURACY_SUFFIX) for k in diff)
    changes: dict[str, FieldChange] = {}
    for key in keys:
        change = _describe_field(db, key, diff, entry)
        if change["old"] != change["new"]:
            changes[key] = change
    return changes
