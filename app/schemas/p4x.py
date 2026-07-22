import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, PlainSerializer, field_validator

from app.models.enums import SubjectMode

IBAN_REGEX = re.compile(r"^[A-Z]{2}\d{2}\s?[\w\s]{4,}$")
BIC_REGEX = re.compile(r"^[A-Za-z0-9]{1,11}$")
HEX_COLOR_REGEX = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Pydantic v2 serializes Decimal to a JSON *string* by default (to avoid
# float precision loss on the wire) — but this API always returned money as
# a plain JSON number, and the vb-intern frontend types/formats it as such.
# This alias keeps Decimal internally (exact arithmetic) while restoring the
# original bare-number wire format on the way out.
MoneyOut = Annotated[
    Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")
]


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


class PartnerRef(BaseModel):
    type: str
    id: int
    cn: str


class CategoryDirectResponse(BaseModel):
    id: int
    p4x_category_id: int
    amount: MoneyOut


class CategoryFilterShortResponse(BaseModel):
    id: int
    name: str
    p4x_account_id: int
    p4x_account_label: str | None
    iban: str | None
    min_amount: MoneyOut | None
    max_amount: MoneyOut | None
    subject: str | None
    subject_mode: SubjectMode
    p4x_category_id: int
    hitCount: int  # noqa: N815


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


class AccountResponse(BaseModel):
    id: int
    iban: str
    bic: str | None
    label: str | None
    init_date: str | None
    init_balance: MoneyOut
    balance: MoneyOut
    transactions_count: int
    transactions_latest: str | None


class AccountSaveRequest(BaseModel):
    iban: str = Field(..., max_length=34)
    bic: str = Field(..., max_length=11)
    label: str = Field(..., max_length=32)
    init_date: date
    init_balance: Decimal = Field(
        ..., ge=-999999999, le=999999999, max_digits=12, decimal_places=2
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
        if v > datetime.now(UTC).date():
            msg = "Datum darf nicht in der Zukunft liegen."
            raise ValueError(msg)
        return v


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------


class TransactionResponse(BaseModel):
    id: int
    booking: str | None
    valuation: str | None
    iban: str
    amount: MoneyOut
    subject: str
    p4x_account_id: int
    p4x_account_cn: str
    p4x_account_iban: str
    comment: str | None = None
    has_attachment: bool = False
    partner: PartnerRef | None = None
    delegating_partner: PartnerRef | None = None
    p4x_category_directs: list[CategoryDirectResponse] = []
    p4x_category_filters: list[CategoryFilterShortResponse] = []


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
    p4x_account_id: int
    parsed: bool


class ImportResult(BaseModel):
    given: ImportGiven
    summary: dict[str, int] = {}
    message: str | None = None


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------


class CategoryResponse(BaseModel):
    id: int
    name: str
    label: str
    background_color: str
    text_color: str
    protected: bool


class CategoryWithUsageResponse(CategoryResponse):
    used: dict[str, int]


class CategorySaveRequest(BaseModel):
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
    id: int
    name: str
    p4x_account_id: int
    p4x_account_label: str | None
    iban: str | None
    min_amount: MoneyOut | None
    max_amount: MoneyOut | None
    subject: str | None
    subject_mode: SubjectMode
    p4x_category_id: int
    hitCount: int  # noqa: N815


class CategoryFilterSaveRequest(BaseModel):
    name: str = Field(..., max_length=64)
    p4x_account_id: int
    iban: str | None = None
    min_amount: Decimal | None = Field(
        None, ge=-999999999, le=999999999, max_digits=12, decimal_places=2
    )
    max_amount: Decimal | None = Field(
        None, ge=-999999999, le=999999999, max_digits=12, decimal_places=2
    )
    subject: str | None = Field(None, max_length=400)
    subject_mode: SubjectMode
    p4x_category_id: int

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


# ---------------------------------------------------------------------------
# Fee
# ---------------------------------------------------------------------------


class FeeResponse(BaseModel):
    start: str
    fee: MoneyOut
    protected: bool


class FeeCreateRequest(BaseModel):
    year: int = Field(..., ge=2015)
    month: int = Field(..., ge=1, le=12)
    fee: Decimal = Field(..., ge=10, le=200, max_digits=12, decimal_places=2)


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


class FeeBalanceResponse(BaseModel):
    start_date: str
    start_balance: MoneyOut
    count: FeeBalanceCount
    sum: FeeBalanceSum
    end_date: str
    end_balance: MoneyOut
    progress: list[FeeProgressEntry]


class FeeMemberResponse(BaseModel):
    id: int
    cn: str
    p4x_init_date: str | None
    p4x_init_balance: MoneyOut | None
    p4x_freed: bool | None
    p4x_comment: str | None
    balance: FeeBalanceResponse | None


class FeeMemberUpdateRequest(BaseModel):
    p4x_init_date: date
    p4x_init_balance: Decimal = Field(
        ..., ge=-999999999, le=999999999, max_digits=12, decimal_places=2
    )
    p4x_freed: bool = False
    p4x_comment: str | None = Field(None, max_length=250)


class DebtorResponse(BaseModel):
    id: int
    cn: str
    balance: MoneyOut


# ---------------------------------------------------------------------------
# Partner
# ---------------------------------------------------------------------------


class PartnerSearchResult(BaseModel):
    type: str
    id: int
    label: str


class SetPartnerRequest(BaseModel):
    partner: PartnerRef | None = None
    hasDelegatingPartner: bool = False  # noqa: N815
    delegatingPartner: PartnerRef | None = None  # noqa: N815


class TransactionUpdateRequest(BaseModel):
    comment: str | None = Field(None, max_length=250)
    delete_attachment: bool = False


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


class SummaryOrderRequest(BaseModel):
    start: date
    end: date

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
        if v > datetime.now(UTC).date():
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
