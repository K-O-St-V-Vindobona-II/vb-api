import re
import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, PlainSerializer, field_validator

from app.core.datetime_utils import local_today
from app.models.enums import SubjectMode
from app.schemas.base import StrictInputModel

IBAN_REGEX = re.compile(r"^[A-Z]{2}\d{2}\s?[\w\s]{4,}$")
BIC_REGEX = re.compile(r"^[A-Za-z0-9]{1,11}$")
HEX_COLOR_REGEX = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Pydantic v2 serializes Decimal to a JSON *string* by default (to avoid
# float precision loss on the wire) — but this API always returned money as
# a plain JSON number, and the vb-intern frontend types/formats it as such.
# This alias keeps Decimal internally (exact arithmetic) while restoring the
# original bare-number wire format on the way out.
MoneyOut = Annotated[
    Decimal, PlainSerializer(float, return_type=float, when_used="json")
]


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


class PartnerRef(BaseModel):
    type: str = Field(
        ...,
        description="Either 'member' or 'contact' - which of the two `id` refers to.",
    )
    id: uuid.UUID
    cn: str


class CategoryDirectResponse(BaseModel):
    id: uuid.UUID
    p4x_category_id: uuid.UUID
    amount: MoneyOut


class CategoryFilterShortResponse(BaseModel):
    id: uuid.UUID
    name: str
    p4x_account_id: uuid.UUID
    p4x_account_label: str | None
    iban: str | None
    min_amount: MoneyOut | None
    max_amount: MoneyOut | None
    subject: str | None
    subject_mode: SubjectMode
    p4x_category_id: uuid.UUID
    hitCount: int = Field(  # noqa: N815
        ..., description="Number of transactions this filter rule currently matches."
    )


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


class AccountResponse(BaseModel):
    id: uuid.UUID
    iban: str
    bic: str | None
    label: str | None
    init_date: str | None
    init_balance: MoneyOut
    balance: MoneyOut
    transactions_count: int
    transactions_latest: str | None


class AccountSaveRequest(StrictInputModel):
    iban: str = Field(..., max_length=34)
    bic: str = Field(..., max_length=11)
    label: str = Field(..., max_length=32)
    # strict=False on date/Decimal fields: JSON has no native date or
    # decimal type, so both always arrive as a string/number that the
    # model-level strict=True would otherwise reject outright.
    init_date: date = Field(strict=False)
    init_balance: Decimal = Field(
        ...,
        ge=-999999999,
        le=999999999,
        max_digits=12,
        decimal_places=2,
        strict=False,
    )

    @field_validator("iban")
    @classmethod
    def validate_iban(cls, v: str) -> str:
        if not IBAN_REGEX.match(v.replace(" ", "")):
            msg = "Ungültiges IBAN-Format."
            raise ValueError(msg)
        return v

    @field_validator("bic")
    @classmethod
    def validate_bic(cls, v: str) -> str:
        if not BIC_REGEX.match(v):
            msg = "Ungültiges BIC-Format."
            raise ValueError(msg)
        return v

    @field_validator("init_date")
    @classmethod
    def validate_init_date(cls, v: date) -> date:
        if v < date(2015, 1, 1):
            msg = "Datum muss nach dem 01.01.2015 liegen."
            raise ValueError(msg)
        if v > local_today():
            msg = "Datum darf nicht in der Zukunft liegen."
            raise ValueError(msg)
        return v


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------


class TransactionResponse(BaseModel):
    id: uuid.UUID
    booking: str | None
    valuation: str | None
    iban: str
    amount: MoneyOut
    subject: str
    p4x_account_id: uuid.UUID
    p4x_account_cn: str
    p4x_account_iban: str
    comment: str | None = None
    has_attachment: bool = False
    partner: PartnerRef | None = None
    delegating_partner: PartnerRef | None = None
    p4x_category_directs: list[CategoryDirectResponse] = Field(
        default=[],
        description=(
            "Categories manually assigned to this transaction, independent of any "
            "filter rule (see /admin/transactions/{id}/set-category-direct)."
        ),
    )
    p4x_category_filters: list[CategoryFilterShortResponse] = Field(
        default=[],
        description=(
            "Categories this transaction currently matches via an active filter "
            "rule (see /admin/category-filters)."
        ),
    )


class TransactionRawResponse(BaseModel):
    raw: str | None


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class PaginatedTransactions(BaseModel):
    items: list[TransactionResponse]
    total: int
    page: int
    per_page: int


class TransactionsByMonthResponse(PaginatedTransactions):
    """GET .../by-month/{year}/{month}'s response - the one paginated
    transaction listing that also carries the account's start/end balance
    for that month."""

    startbalance: MoneyOut
    endbalance: MoneyOut


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


class WarningsResponse(BaseModel):
    count: int
    preview: list[TransactionResponse]


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


class ImportGiven(BaseModel):
    p4x_account_id: uuid.UUID
    parsed: bool = Field(
        ..., description="Whether the file parsed as a George-Bank export."
    )


class ImportResult(BaseModel):
    given: ImportGiven
    summary: dict[str, int] = {}
    message: str | None = None
    # Only set on a successful import (see import_transactions in
    # app/api/router_includes/p4x.py) - the failure branch (given.parsed=False)
    # only ever sets message.
    account: AccountResponse | None = None


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------


class CategoryResponse(BaseModel):
    id: uuid.UUID
    name: str
    label: str
    background_color: str
    text_color: str
    protected: bool = Field(
        ..., description="System category that cannot be deleted (see delete_category)."
    )


class CategoryWithUsageResponse(CategoryResponse):
    used: dict[str, int]


class CategorySaveRequest(StrictInputModel):
    name: str = Field(..., max_length=64)
    label: str = Field(..., max_length=32)
    background_color: str
    text_color: str

    @field_validator("background_color", "text_color")
    @classmethod
    def validate_hex_color(cls, v: str) -> str:
        if not HEX_COLOR_REGEX.match(v):
            msg = "Ungültiges Farbformat. Erlaubt: #RGB oder #RRGGBB."
            raise ValueError(msg)
        return v


# ---------------------------------------------------------------------------
# Category Filter
# ---------------------------------------------------------------------------


class CategoryFilterResponse(BaseModel):
    id: uuid.UUID
    name: str
    p4x_account_id: uuid.UUID
    p4x_account_label: str | None
    iban: str | None
    min_amount: MoneyOut | None
    max_amount: MoneyOut | None
    subject: str | None
    subject_mode: SubjectMode
    p4x_category_id: uuid.UUID
    hitCount: int = Field(  # noqa: N815
        ..., description="Number of transactions this filter rule currently matches."
    )


class CategoryFilterSaveRequest(StrictInputModel):
    name: str = Field(..., max_length=64)
    # strict=False: JSON has no native UUID type either, so it always
    # arrives as a plain hex string, not a uuid.UUID instance.
    p4x_account_id: uuid.UUID = Field(strict=False)
    iban: str | None = None
    # strict=False: see AccountSaveRequest.init_balance above.
    min_amount: Decimal | None = Field(
        None,
        ge=-999999999,
        le=999999999,
        max_digits=12,
        decimal_places=2,
        strict=False,
    )
    max_amount: Decimal | None = Field(
        None,
        ge=-999999999,
        le=999999999,
        max_digits=12,
        decimal_places=2,
        strict=False,
    )
    subject: str | None = Field(None, max_length=400)
    # strict=False: the wire value is the enum's raw string, not an actual
    # SubjectMode instance — JSON has no native enum type either.
    subject_mode: SubjectMode = Field(strict=False)
    # strict=False: same reasoning as p4x_account_id above.
    p4x_category_id: uuid.UUID = Field(strict=False)

    @field_validator("iban", mode="before")
    @classmethod
    def validate_filter_iban(cls, v: str | None) -> str | None:
        if v and not re.match(r"^[a-zA-Z]{2}[0-9 ]{18,23}$", v):
            msg = "Ungültiges IBAN-Format."
            raise ValueError(msg)
        return v


class FilterHitResponse(BaseModel):
    booking: str | None
    amount: MoneyOut
    subject: str
    iban: str


class Filter2DirectPreviewResponse(BaseModel):
    # camelCase on the wire (pre-existing key, kept as-is for the frontend)
    warningsCount: int  # noqa: N815
    filter: CategoryFilterResponse
    category: CategoryResponse
    hits: list[FilterHitResponse]


class Filter2DirectResultResponse(BaseModel):
    hits: list[FilterHitResponse]


# ---------------------------------------------------------------------------
# Fee
# ---------------------------------------------------------------------------


class FeeResponse(BaseModel):
    start: str
    fee: MoneyOut
    protected: bool


class FeeCreateRequest(StrictInputModel):
    year: int = Field(..., ge=2015)
    month: int = Field(..., ge=1, le=12)
    fee: Decimal = Field(
        ..., ge=10, le=200, max_digits=12, decimal_places=2, strict=False
    )


# ---------------------------------------------------------------------------
# Fee Member
# ---------------------------------------------------------------------------


class FeeBalanceCount(BaseModel):
    fees: int
    payments: int


class FeeBalanceSum(BaseModel):
    fees: MoneyOut
    payments: MoneyOut


class FeeProgressEntry(BaseModel):
    type: str
    booking: str
    amount: MoneyOut
    balance: MoneyOut


class FeeBalanceResponse(BaseModel):
    start_date: str
    start_balance: MoneyOut
    count: FeeBalanceCount
    sum: FeeBalanceSum
    end_date: str
    end_balance: MoneyOut
    progress: list[FeeProgressEntry]


class FeeMemberSelfResponse(BaseModel):
    """Self-service shape of a fee member's account - deliberately omits
    p4x_comment, which is an admin-internal note not meant for the member
    to see."""

    id: uuid.UUID
    cn: str
    p4x_init_date: str | None = Field(
        ...,
        description=(
            "Month fee tracking starts from for this member - defaults to their "
            "Philistrierungsdatum if never explicitly set."
        ),
    )
    p4x_init_balance: MoneyOut | None
    p4x_freed: bool | None = Field(
        ..., description="True if this member is exempted from paying membership fees."
    )
    balance: FeeBalanceResponse | None


class FeeMemberResponse(FeeMemberSelfResponse):
    p4x_comment: str | None = Field(
        ...,
        description=(
            "Admin-internal note about this member's fee account, never shown to "
            "the member themselves (see FeeMemberSelfResponse)."
        ),
    )


class FeeMemberUpdateRequest(StrictInputModel):
    p4x_init_date: date = Field(strict=False)
    p4x_init_balance: Decimal = Field(
        ...,
        ge=-999999999,
        le=999999999,
        max_digits=12,
        decimal_places=2,
        strict=False,
    )
    p4x_freed: bool = False
    p4x_comment: str | None = Field(None, max_length=250)


class FeeBalanceListItem(BaseModel):
    id: uuid.UUID
    cn: str
    p4x_freed: bool
    balance: MoneyOut


class FeeMemberSearchResultItem(BaseModel):
    id: uuid.UUID
    label: str


class FeeMemberSearchResponse(BaseModel):
    data: list[FeeMemberSearchResultItem]


# ---------------------------------------------------------------------------
# Partner
# ---------------------------------------------------------------------------


class PartnerSearchResult(BaseModel):
    type: str
    id: uuid.UUID
    label: str


class SetPartnerRequest(StrictInputModel):
    partner: PartnerRef | None = None
    hasDelegatingPartner: bool = Field(  # noqa: N815
        default=False,
        description=(
            "Whether a second partner made this payment on the primary partner's "
            "behalf (e.g. a spouse paying for both memberships in one transaction)."
        ),
    )
    delegatingPartner: PartnerRef | None = Field(  # noqa: N815
        default=None,
        description="The delegating partner, if hasDelegatingPartner is true.",
    )


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


class SummaryOrderRequest(StrictInputModel):
    start: date = Field(strict=False)
    end: date = Field(strict=False)

    @field_validator("start")
    @classmethod
    def validate_start(cls, v: date) -> date:
        if v < date(2015, 1, 1):
            msg = "Startdatum muss nach dem 01.01.2015 liegen."
            raise ValueError(msg)
        return v

    @field_validator("end")
    @classmethod
    def validate_end(cls, v: date) -> date:
        if v > local_today():
            msg = "Enddatum darf nicht in der Zukunft liegen."
            raise ValueError(msg)
        return v


# ---------------------------------------------------------------------------
# SumUp
# ---------------------------------------------------------------------------


class SumUpBalanceResponse(BaseModel):
    in_count: int
    in_sum: MoneyOut
    out_count: int
    out_sum: MoneyOut
    latest: str | None


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class DashboardResponse(BaseModel):
    accounts: list[AccountResponse]
    warnings_partner: WarningsResponse
    warnings_category: WarningsResponse
    categories: list[CategoryResponse]
