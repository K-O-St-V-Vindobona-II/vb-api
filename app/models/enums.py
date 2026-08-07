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


class FeeInterval(StrEnum):
    """How often a fee member normally pays their membership dues. Governs
    the advance-payment ceiling in p4x_fee_balance_service.calculate_fee_balance
    for payments booked on/after FEE_INTERVAL_CEILING_CUTOVER: a single
    payment is credited toward the fee balance only up to the sum of the
    actual monthly fee amounts over the trailing `interval_months` months
    (not a flat `interval_months * fee_for_month(booking)` multiplication —
    that would misprice a member whose fee rate changed partway through
    their prepayment interval). UNLIMITED skips that ceiling entirely and
    must never be assigned automatically (see the interval-detection script
    referenced in that module) — it's a deliberate, manually-set trust
    decision for a specific, known member. BIENNIAL was considered and
    dropped: the only real-data candidate for it turned out to be an
    irregular lump-sum payer, not a genuine two-year cadence (see the P4x
    fee/donation redesign plan for that investigation)."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMIANNUAL = "semiannual"
    ANNUAL = "annual"
    UNLIMITED = "unlimited"


FEE_INTERVAL_MONTHS: dict[FeeInterval, int] = {
    FeeInterval.MONTHLY: 1,
    FeeInterval.QUARTERLY: 3,
    FeeInterval.SEMIANNUAL: 6,
    FeeInterval.ANNUAL: 12,
    # FeeInterval.UNLIMITED intentionally has no entry — callers must check
    # for UNLIMITED before looking up a month count, not fall through to a
    # KeyError or a bogus default.
}
