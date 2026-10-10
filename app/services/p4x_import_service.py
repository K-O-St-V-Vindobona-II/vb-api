from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, TypedDict, cast

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.p4x_account import P4xAccount

from app.models.p4x_transaction import P4xTransaction
from app.schemas.p4x import ImportGiven, ImportResult
from app.services import p4x_category_service, p4x_response_builders

GEORGE_BIC = "GIBAATWWXXX"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class ParsedTransactionPayload(TypedDict):
    """Normalized fields extracted from one George bank statement entry."""

    booking: date
    valuation: date
    iban: str
    amount: str
    subject: str


class ParsedTransactionEntry(TypedDict):
    payload: ParsedTransactionPayload
    raw: str


@dataclass
class ParseResult:
    success: bool
    message: str
    entries: list[ParsedTransactionEntry] = field(
        default_factory=list[ParsedTransactionEntry]
    )


class _CorruptEntryError(ValueError):
    """One bank entry cannot be read; the message says why."""


# The amount column is NUMERIC(12,2); a bank export states the value in minor
# units with a precision of 2 (sometimes 0 or 3), never more than a few digits.
_MAX_PRECISION = 9
_MAX_ABSOLUTE_AMOUNT = Decimal(10) ** 10


def _child(node: object, key: str, path: tuple[str, ...]) -> object:
    """Value of `key` inside `node`, or a corrupt-entry error naming `path`."""
    if not isinstance(node, dict) or key not in node:
        msg = f"missing field: {'.'.join(path)}"
        raise _CorruptEntryError(msg)
    return cast("dict[str, object]", node)[key]


def _field(struct: dict[str, object], *path: str) -> object:
    """Value at `path` inside one entry, or a corrupt-entry error naming it."""
    node: object = struct
    for key in path:
        node = _child(node, key, path)
    return node


def _parse_amount(value: object, precision: object) -> Decimal:
    """The amount in major units, from the bank's (value, precision) pair.

    Refuses anything that is not a finite number that fits the column:
    "NaN" and "Infinity" convert to a Decimal without an error, and a NaN
    stored in the database turns every balance of the account into NaN.
    """
    try:
        exponent = int(str(precision).strip())
    except ValueError as exc:
        msg = "invalid field: amount.precision"
        raise _CorruptEntryError(msg) from exc
    if not 0 <= exponent <= _MAX_PRECISION:
        msg = "invalid field: amount.precision"
        raise _CorruptEntryError(msg)
    try:
        amount = Decimal(str(value).strip()) / (Decimal(10) ** exponent)
    except ArithmeticError as exc:
        msg = "invalid field: amount.value"
        raise _CorruptEntryError(msg) from exc
    if not amount.is_finite() or abs(amount) >= _MAX_ABSOLUTE_AMOUNT:
        msg = "invalid field: amount.value"
        raise _CorruptEntryError(msg)
    return amount


def _booking_date(struct: dict[str, object], name: str) -> date:
    text = str(_field(struct, name)).strip()
    try:
        return _parse_date_string(text)
    except ValueError as exc:
        msg = f"invalid field: {name}"
        raise _CorruptEntryError(msg) from exc


def _subject(struct: dict[str, object]) -> str:
    """The longer of the two reference fields (either may be missing)."""
    ref = str(struct.get("reference") or "")
    recv = str(struct.get("receiverReference") or "")
    return (ref if len(ref) > len(recv) else recv).strip()


def _parse_entry(struct: object) -> ParsedTransactionEntry:
    if not isinstance(struct, dict):
        msg = "entry is not an object"
        raise _CorruptEntryError(msg)
    entry = cast("dict[str, object]", struct)

    booking = _booking_date(entry, "booking")
    valuation = _booking_date(entry, "valuation")
    if not isinstance(_field(entry, "partnerAccount"), dict):
        # The bank sends null for entries without a counter account (cash).
        entry["partnerAccount"] = {"iban": ""}
    iban = str(_field(entry, "partnerAccount", "iban")).strip()
    _field(entry, "amount")
    amount = _parse_amount(
        _field(entry, "amount", "value"), _field(entry, "amount", "precision")
    )
    if "reference" not in entry and "receiverReference" not in entry:
        msg = "missing field: reference or receiverReference"
        raise _CorruptEntryError(msg)

    return {
        "payload": {
            "booking": booking,
            "valuation": valuation,
            "iban": iban,
            "amount": f"{amount:.2f}",
            "subject": _subject(entry),
        },
        "raw": json.dumps(entry),
    }


def parse_george_json(bic: str, raw_json: str) -> ParseResult:
    if bic != GEORGE_BIC:
        return ParseResult(
            success=False, message=f"No parser method found for BIC {bic}"
        )

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError, TypeError:
        return ParseResult(success=False, message="failed to parse given raw json data")

    if not isinstance(data, list):
        return ParseResult(success=False, message="failed to parse given raw json data")

    try:
        entries = [_parse_entry(struct) for struct in cast("list[object]", data)]
    except _CorruptEntryError as exc:
        return ParseResult(
            success=False,
            message=f"given data contains at least one corrupt entry: {exc}",
        )

    return ParseResult(success=True, message="finished successfully", entries=entries)


def _parse_date_string(date_str: str) -> date:
    return date.fromisoformat(date_str[:10])


# ---------------------------------------------------------------------------
# SHA256 hash of a transaction. The format must not change: the stored hashes
# were computed over exactly this text.
# ---------------------------------------------------------------------------


def _date_to_carbon_json(booking_date: date, original_date_str: str) -> str:
    """Format a booking date as the UTC timestamp the stored hashes use.

    The original UTC offset of the bank's text is applied first, then the time
    is converted to UTC and written as 'YYYY-MM-DDTHH:MM:SS.000000Z'.
    """
    m = re.match(
        r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?([+-])(\d{2})(\d{2})",
        original_date_str.strip(),
    )
    if not m:
        return f"{booking_date.isoformat()}T00:00:00.000000Z"

    dt = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")
    sign = 1 if m.group(3) == "+" else -1
    tz = timezone(
        timedelta(hours=sign * int(m.group(4)), minutes=sign * int(m.group(5)))
    )
    dt_utc = dt.replace(tzinfo=tz).astimezone(UTC)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S") + ".000000Z"


def _json_encode_escaping_slashes(obj: list[str]) -> str:
    """Encode as compact ASCII JSON with escaped slashes.

    That is the exact text the stored hashes were computed over."""
    s = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    return s.replace("/", "\\/")


def compute_transaction_hash(
    booking_carbon_json: str,
    valuation_carbon_json: str,
    iban: str,
    amount_str: str,
    subject: str,
) -> str:
    payload = _json_encode_escaping_slashes(
        [
            booking_carbon_json,
            valuation_carbon_json,
            iban,
            amount_str,
            subject,
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _distinguish_repeated_entry(
    sha256_hash: str, seen_hashes: dict[str, int], summary: dict[str, int]
) -> str:
    """Hash for the n-th occurrence of an identical entry in one file.

    Two payments with the same day, counter account, amount and reference
    produce the same hash, and the hash column is unique: without this the
    second one ends in a database error and the whole file cannot be
    imported. The first occurrence keeps its hash (so everything imported
    before stays valid); later ones get a hash that depends on their position
    among the identical entries, which is the same again on a re-import of
    the file.
    """
    occurrence = seen_hashes.get(sha256_hash, 0) + 1
    seen_hashes[sha256_hash] = occurrence
    if occurrence == 1:
        return sha256_hash
    summary["repeated_in_file"] = summary.get("repeated_in_file", 0) + 1
    return hashlib.sha256(f"{sha256_hash}#{occurrence}".encode()).hexdigest()


def import_transactions(
    db: Session,
    account: P4xAccount,
    parsed_entries: list[ParsedTransactionEntry],
    original_structs: list[dict[str, object]],
) -> dict[str, int]:
    """Import parsed transactions into the database.

    Returns a summary dict with counts by status.
    """
    summary: dict[str, int] = {}
    seen_hashes: dict[str, int] = {}

    for i, entry in enumerate(parsed_entries):
        payload = entry["payload"]
        raw = entry["raw"]

        summary["giventotal"] = summary.get("giventotal", 0) + 1

        mandatory = {"booking", "valuation", "iban", "amount", "subject"}
        if not mandatory.issubset(payload.keys()):
            summary["error"] = summary.get("error", 0) + 1
            continue

        amount_decimal = Decimal(payload["amount"])
        if amount_decimal == 0:
            summary["zero_skipped"] = summary.get("zero_skipped", 0) + 1
            continue

        orig_struct = original_structs[i]
        booking_carbon = _date_to_carbon_json(
            payload["booking"], str(orig_struct.get("booking", ""))
        )
        valuation_carbon = _date_to_carbon_json(
            payload["valuation"], str(orig_struct.get("valuation", ""))
        )

        sha256_hash = compute_transaction_hash(
            booking_carbon,
            valuation_carbon,
            payload["iban"],
            payload["amount"],
            payload["subject"],
        )
        sha256_hash = _distinguish_repeated_entry(sha256_hash, seen_hashes, summary)

        if account.init_date is not None and payload["booking"] < account.init_date:
            db.query(P4xTransaction).filter(
                P4xTransaction.sha256_hash == sha256_hash,
            ).update({"deleted_at": datetime.now(UTC)})
            summary["before_init_date"] = summary.get("before_init_date", 0) + 1
            continue

        # Deleted rows are looked up too: the hash is unique over all rows, so
        # an entry that was set aside earlier (before the account's start date)
        # is brought back instead of inserted a second time.
        existing = (
            db.query(P4xTransaction)
            .filter(P4xTransaction.sha256_hash == sha256_hash)
            .first()
        )

        if existing:
            status = "existing"
            if existing.deleted_at is not None:
                existing.deleted_at = None
                status = "restored"
            existing.booking = payload["booking"]
            existing.valuation = payload["valuation"]
            existing.iban = payload["iban"]
            existing.amount = amount_decimal
            existing.subject = payload["subject"]
            existing.raw = raw
            if existing.p4x_account_id != account.id:
                existing.p4x_account_id = account.id
                status = "existing_with_new_binding"
        else:
            status = "new"
            tx = P4xTransaction(
                sha256_hash=sha256_hash,
                booking=payload["booking"],
                valuation=payload["valuation"],
                iban=payload["iban"],
                amount=amount_decimal,
                subject=payload["subject"],
                p4x_account_id=account.id,
                raw=raw,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            db.add(tx)

        summary[status] = summary.get(status, 0) + 1

    db.flush()
    return summary


def import_and_apply_filters(
    db: Session,
    account: P4xAccount,
    parsed_entries: list[ParsedTransactionEntry],
    original_structs: list[dict[str, object]],
) -> dict[str, int]:
    """Import transactions and re-apply category filters as one atomic
    transaction - financial data, so a failure applying filters must not
    leave the import itself half-committed."""
    summary = import_transactions(db, account, parsed_entries, original_structs)
    p4x_category_service.apply_all_category_filters_core(db)
    db.commit()
    return summary


def run_george_import(db: Session, account: P4xAccount, raw_json: str) -> ImportResult:
    """Parse an uploaded George export and, when valid, import it into `account`."""
    parse_result = parse_george_json(account.bic or "", raw_json)
    if not parse_result.success:
        return ImportResult(
            given=ImportGiven(p4x_account_id=account.id, parsed=False),
            message=parse_result.message,
        )

    summary = import_and_apply_filters(
        db, account, parse_result.entries, json.loads(raw_json)
    )
    db.refresh(account)
    return ImportResult(
        given=ImportGiven(p4x_account_id=account.id, parsed=True),
        summary=summary,
        account=p4x_response_builders.build_account_response(db, account),
    )
