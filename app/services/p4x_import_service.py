from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.p4x_account import P4xAccount

from app.models.p4x_transaction import P4xTransaction
from app.services import p4x_category_service

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
    entries: list[ParsedTransactionEntry] = field(default_factory=list)


def parse_george_json(bic: str, raw_json: str) -> ParseResult:  # noqa: C901, PLR0911, PLR0912
    if bic != GEORGE_BIC:
        return ParseResult(
            success=False, message=f"No parser method found for BIC {bic}"
        )

    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return ParseResult(success=False, message="failed to parse given raw json data")

    if not isinstance(data, list):
        return ParseResult(success=False, message="failed to parse given raw json data")

    result: list[ParsedTransactionEntry] = []

    for struct in data:
        if "booking" not in struct:
            return ParseResult(
                success=False,
                message=(
                    "given data contains at least one corrupt entry:"
                    " missing field: booking"
                ),
            )
        if "valuation" not in struct:
            return ParseResult(
                success=False,
                message=(
                    "given data contains at least one corrupt entry:"
                    " missing field: valuation"
                ),
            )
        if "partnerAccount" not in struct:
            return ParseResult(
                success=False,
                message=(
                    "given data contains at least one corrupt entry:"
                    " missing field: partnerAccount"
                ),
            )
        if not isinstance(struct["partnerAccount"], dict):
            struct["partnerAccount"] = {"iban": ""}
        if "iban" not in struct["partnerAccount"]:
            return ParseResult(
                success=False,
                message=(
                    "given data contains at least one corrupt entry:"
                    " missing field: partnerAccount.iban"
                ),
            )
        if "amount" not in struct:
            return ParseResult(
                success=False,
                message=(
                    "given data contains at least one corrupt entry:"
                    " missing field: amount"
                ),
            )
        if "value" not in struct["amount"]:
            return ParseResult(
                success=False,
                message=(
                    "given data contains at least one corrupt entry:"
                    " missing field: amount.value"
                ),
            )
        if "precision" not in struct["amount"]:
            return ParseResult(
                success=False,
                message=(
                    "given data contains at least one corrupt entry:"
                    " missing field: amount.precision"
                ),
            )
        if "reference" not in struct and "receiverReference" not in struct:
            return ParseResult(
                success=False,
                message=(
                    "given data contains at least one corrupt entry:"
                    " missing field: reference or receiverReference"
                ),
            )

        ref = struct.get("reference") or ""
        recv = struct.get("receiverReference") or ""
        if ref and recv:
            subject = ref if len(ref) > len(recv) else recv
        elif ref:
            subject = ref
        elif recv:
            subject = recv
        else:
            subject = ""

        precision = int(str(struct["amount"]["precision"]).strip())
        raw_value = Decimal(str(struct["amount"]["value"]).strip())
        amount = raw_value / (Decimal(10) ** precision)

        result.append(
            {
                "payload": {
                    "booking": _parse_date_string(str(struct["booking"]).strip()),
                    "valuation": _parse_date_string(str(struct["valuation"]).strip()),
                    "iban": str(struct["partnerAccount"]["iban"]).strip(),
                    "amount": f"{amount:.2f}",
                    "subject": subject.strip(),
                },
                "raw": json.dumps(struct),
            }
        )

    return ParseResult(success=True, message="finished successfully", entries=result)


def _parse_date_string(date_str: str) -> date:
    return date.fromisoformat(date_str[:10])


# ---------------------------------------------------------------------------
# SHA256 hash (must match PHP Carbon toJSON + json_encode behavior)
# ---------------------------------------------------------------------------


def _date_to_carbon_json(booking_date: date, original_date_str: str) -> str:
    """Convert a date to Carbon 3's toJSON() UTC format.

    Carbon::parse() preserves the original timezone, then toJSON() converts to
    UTC and formats as 'YYYY-MM-DDTHH:MM:SS.000000Z'.
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


def _php_json_encode(obj: list[str]) -> str:
    """Replicate PHP's default json_encode behavior."""
    s = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    return s.replace("/", "\\/")


def compute_transaction_hash(
    booking_carbon_json: str,
    valuation_carbon_json: str,
    iban: str,
    amount_str: str,
    subject: str,
) -> str:
    payload = _php_json_encode(
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

        if account.init_date is not None and payload["booking"] < account.init_date:
            db.query(P4xTransaction).filter(
                P4xTransaction.sha256_hash == sha256_hash,
            ).update({"deleted_at": datetime.now(UTC)})
            summary["before_init_date"] = summary.get("before_init_date", 0) + 1
            continue

        existing = (
            db.query(P4xTransaction)
            .filter(
                P4xTransaction.sha256_hash == sha256_hash,
                P4xTransaction.deleted_at.is_(None),
            )
            .first()
        )

        if existing:
            status = "existing"
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
