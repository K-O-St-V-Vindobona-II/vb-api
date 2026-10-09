"""Pure unit tests for Pydantic validators in app/schemas/p4x.py."""

import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.core.datetime_utils import local_today
from app.schemas.p4x import (
    AccountSaveRequest,
    CategoryFilterSaveRequest,
    CategorySaveRequest,
    FeeCreateRequest,
    FeeMemberUpdateRequest,
    SetPartnerRequest,
    SummaryOrderRequest,
)

# ---------------------------------------------------------------------------
# Helpers — minimal valid payloads
# ---------------------------------------------------------------------------


def _valid_account_data(**overrides: object) -> dict[str, object]:
    """Return the minimum valid AccountSaveRequest payload."""
    base: dict[str, object] = {
        "iban": "AT611904300234573201",
        "bic": "GIBAATWWXXX",
        "label": "Girokonto",
        "init_date": date(2020, 1, 1),
        "init_balance": 0.0,
    }
    base.update(overrides)
    return base


def _valid_filter_data(**overrides: object) -> dict[str, object]:
    """Return the minimum valid CategoryFilterSaveRequest payload."""
    base: dict[str, object] = {
        "name": "Testfilter",
        "p4x_account_id": uuid.uuid4(),
        "subject_mode": "contains",
        "p4x_category_id": uuid.uuid4(),
    }
    base.update(overrides)
    return base


def _valid_summary_data(**overrides: object) -> dict[str, object]:
    """Return the minimum valid SummaryOrderRequest payload."""
    base: dict[str, object] = {
        "start": date(2020, 1, 1),
        "end": date(2025, 12, 31),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# AccountSaveRequest — validate_iban
# ---------------------------------------------------------------------------


class TestAccountIban:
    def test_valid_iban_passes(self) -> None:
        data = _valid_account_data(iban="AT611904300234573201")
        a = AccountSaveRequest(**data)
        assert a.iban == "AT611904300234573201"

    def test_invalid_iban_no_country_code_rejected(self) -> None:
        data = _valid_account_data(iban="1234567890")
        with pytest.raises(ValidationError, match="IBAN"):
            AccountSaveRequest(**data)

    def test_invalid_iban_too_short_rejected(self) -> None:
        data = _valid_account_data(iban="AT61")
        with pytest.raises(ValidationError, match="IBAN"):
            AccountSaveRequest(**data)

    def test_invalid_iban_special_chars_rejected(self) -> None:
        data = _valid_account_data(iban="AT!!invalid##format")
        with pytest.raises(ValidationError, match="IBAN"):
            AccountSaveRequest(**data)


# ---------------------------------------------------------------------------
# AccountSaveRequest — validate_bic
# ---------------------------------------------------------------------------


class TestAccountBic:
    def test_valid_bic_passes(self) -> None:
        data = _valid_account_data(bic="GIBAATWWXXX")
        a = AccountSaveRequest(**data)
        assert a.bic == "GIBAATWWXXX"

    def test_invalid_bic_special_chars_rejected(self) -> None:
        data = _valid_account_data(bic="GIBA@TW!XX")
        with pytest.raises(ValidationError, match="BIC"):
            AccountSaveRequest(**data)

    def test_invalid_bic_too_long_rejected(self) -> None:
        data = _valid_account_data(bic="A" * 12)
        with pytest.raises(ValidationError, match=r"11 characters|string_too_long"):
            AccountSaveRequest(**data)


# ---------------------------------------------------------------------------
# AccountSaveRequest — validate_init_date
# ---------------------------------------------------------------------------


class TestAccountInitDate:
    def test_valid_date_passes(self) -> None:
        data = _valid_account_data(init_date=date(2020, 6, 15))
        a = AccountSaveRequest(**data)
        assert a.init_date == date(2020, 6, 15)

    def test_date_before_2015_rejected(self) -> None:
        data = _valid_account_data(init_date=date(2014, 12, 31))
        with pytest.raises(ValidationError, match=re.escape("01.01.2015")):
            AccountSaveRequest(**data)

    def test_date_exactly_2015_passes(self) -> None:
        data = _valid_account_data(init_date=date(2015, 1, 1))
        a = AccountSaveRequest(**data)
        assert a.init_date == date(2015, 1, 1)

    def test_future_date_rejected(self) -> None:
        future = local_today() + timedelta(days=30)
        data = _valid_account_data(init_date=future)
        with pytest.raises(ValidationError, match="Zukunft"):
            AccountSaveRequest(**data)

    def test_today_passes(self) -> None:
        data = _valid_account_data(init_date=local_today())
        a = AccountSaveRequest(**data)
        assert a.init_date == local_today()

    def test_today_passes_using_configured_app_timezone_not_utc(self) -> None:
        """Regression (2026-08-15 timezone audit): previously compared
        against datetime.now(UTC).date() — an init_date of Vienna's actual
        "today" could be wrongly rejected as "in the future" for up to 2h
        after Vienna midnight, when UTC's date still lags a day behind.
        Patching local_today() directly (rather than the real system
        clock) keeps this deterministic regardless of when the suite
        runs."""
        fixed_today = date(2026, 6, 15)
        with patch("app.schemas.p4x.local_today", return_value=fixed_today):
            data = _valid_account_data(init_date=fixed_today)
            a = AccountSaveRequest(**data)
        assert a.init_date == fixed_today


# ---------------------------------------------------------------------------
# CategoryFilterSaveRequest — validate_filter_iban
# ---------------------------------------------------------------------------


class TestFilterIban:
    def test_valid_iban_passes(self) -> None:
        data = _valid_filter_data(iban="AT61 1904 3002 3457 3201")
        f = CategoryFilterSaveRequest(**data)
        assert f.iban == "AT61 1904 3002 3457 3201"

    def test_none_iban_passes(self) -> None:
        data = _valid_filter_data(iban=None)
        f = CategoryFilterSaveRequest(**data)
        assert f.iban is None

    def test_invalid_filter_iban_rejected(self) -> None:
        data = _valid_filter_data(iban="INVALID_IBAN")
        with pytest.raises(ValidationError, match="IBAN"):
            CategoryFilterSaveRequest(**data)

    def test_numeric_only_iban_rejected(self) -> None:
        data = _valid_filter_data(iban="1234567890123456789012")
        with pytest.raises(ValidationError, match="IBAN"):
            CategoryFilterSaveRequest(**data)


# ---------------------------------------------------------------------------
# CategoryFilterSaveRequest — validate_subject_mode
# ---------------------------------------------------------------------------


class TestFilterSubjectMode:
    def test_equals_passes(self) -> None:
        data = _valid_filter_data(subject_mode="equals")
        f = CategoryFilterSaveRequest(**data)
        assert f.subject_mode == "equals"

    def test_contains_passes(self) -> None:
        data = _valid_filter_data(subject_mode="contains")
        f = CategoryFilterSaveRequest(**data)
        assert f.subject_mode == "contains"

    def test_starts_passes(self) -> None:
        data = _valid_filter_data(subject_mode="starts")
        f = CategoryFilterSaveRequest(**data)
        assert f.subject_mode == "starts"

    def test_invalid_mode_rejected(self) -> None:
        data = _valid_filter_data(subject_mode="regex")
        with pytest.raises(ValidationError, match="subject_mode"):
            CategoryFilterSaveRequest(**data)

    def test_empty_mode_rejected(self) -> None:
        data = _valid_filter_data(subject_mode="")
        with pytest.raises(ValidationError, match="subject_mode"):
            CategoryFilterSaveRequest(**data)


# ---------------------------------------------------------------------------
# SummaryOrderRequest — validate_start
# ---------------------------------------------------------------------------


class TestSummaryStart:
    def test_valid_start_passes(self) -> None:
        data = _valid_summary_data(start=date(2020, 1, 1))
        s = SummaryOrderRequest(**data)
        assert s.start == date(2020, 1, 1)

    def test_start_before_2015_rejected(self) -> None:
        data = _valid_summary_data(start=date(2014, 12, 31))
        with pytest.raises(ValidationError, match=re.escape("01.01.2015")):
            SummaryOrderRequest(**data)

    def test_start_exactly_2015_passes(self) -> None:
        data = _valid_summary_data(start=date(2015, 1, 1))
        s = SummaryOrderRequest(**data)
        assert s.start == date(2015, 1, 1)


# ---------------------------------------------------------------------------
# SummaryOrderRequest — validate_end
# ---------------------------------------------------------------------------


class TestSummaryEnd:
    def test_valid_end_passes(self) -> None:
        data = _valid_summary_data(end=date(2025, 6, 30))
        s = SummaryOrderRequest(**data)
        assert s.end == date(2025, 6, 30)

    def test_end_in_future_rejected(self) -> None:
        future = local_today() + timedelta(days=30)
        data = _valid_summary_data(end=future)
        with pytest.raises(ValidationError, match="Zukunft"):
            SummaryOrderRequest(**data)

    def test_end_today_passes(self) -> None:
        data = _valid_summary_data(end=local_today())
        s = SummaryOrderRequest(**data)
        assert s.end == local_today()

    def test_end_today_passes_using_configured_app_timezone_not_utc(self) -> None:
        """Regression (2026-08-15 timezone audit) — see the equivalent test
        in TestAccountInitDate above; same validator pattern, applied here
        to SummaryOrderRequest.end."""
        fixed_today = date(2026, 6, 15)
        with patch("app.schemas.p4x.local_today", return_value=fixed_today):
            data = _valid_summary_data(end=fixed_today)
            s = SummaryOrderRequest(**data)
        assert s.end == fixed_today


# ---------------------------------------------------------------------------
# CategorySaveRequest — validate_hex_color
# ---------------------------------------------------------------------------


def _valid_category_data(**overrides: object) -> dict[str, object]:
    """Return the minimum valid CategorySaveRequest payload."""
    base: dict[str, object] = {
        "name": "test.category",
        "label": "Test",
        "background_color": "#336600",
        "text_color": "#ffffff",
    }
    base.update(overrides)
    return base


class TestCategoryHexColor:
    def test_valid_6_digit_hex_passes(self) -> None:
        data = _valid_category_data(background_color="#ff0000")
        c = CategorySaveRequest(**data)
        assert c.background_color == "#ff0000"

    def test_valid_3_digit_hex_passes(self) -> None:
        data = _valid_category_data(text_color="#f00")
        c = CategorySaveRequest(**data)
        assert c.text_color == "#f00"

    def test_invalid_color_no_hash_rejected(self) -> None:
        data = _valid_category_data(background_color="ff0000")
        with pytest.raises(ValidationError, match="Farbformat"):
            CategorySaveRequest(**data)

    def test_invalid_color_wrong_length_rejected(self) -> None:
        data = _valid_category_data(text_color="#ff00")
        with pytest.raises(ValidationError, match="Farbformat"):
            CategorySaveRequest(**data)

    def test_invalid_color_non_hex_rejected(self) -> None:
        data = _valid_category_data(background_color="#gggggg")
        with pytest.raises(ValidationError, match="Farbformat"):
            CategorySaveRequest(**data)


# ---------------------------------------------------------------------------
# Money fields — decimal_places=2 (Welle 2 Slice 5: float -> Numeric)
# ---------------------------------------------------------------------------


class TestAccountInitBalanceDecimalPlaces:
    def test_two_decimal_places_passes(self) -> None:
        data = _valid_account_data(init_balance=Decimal("123.45"))
        a = AccountSaveRequest(**data)
        assert a.init_balance == Decimal("123.45")

    def test_whole_number_passes(self) -> None:
        data = _valid_account_data(init_balance=Decimal(100))
        a = AccountSaveRequest(**data)
        assert a.init_balance == Decimal(100)

    def test_three_decimal_places_rejected(self) -> None:
        data = _valid_account_data(init_balance=Decimal("123.456"))
        with pytest.raises(ValidationError, match="decimal places"):
            AccountSaveRequest(**data)


class TestFilterAmountDecimalPlaces:
    def test_min_amount_two_decimal_places_passes(self) -> None:
        data = _valid_filter_data(min_amount=Decimal("10.50"))
        f = CategoryFilterSaveRequest(**data)
        assert f.min_amount == Decimal("10.50")

    def test_min_amount_three_decimal_places_rejected(self) -> None:
        data = _valid_filter_data(min_amount=Decimal("10.505"))
        with pytest.raises(ValidationError, match="decimal places"):
            CategoryFilterSaveRequest(**data)

    def test_max_amount_three_decimal_places_rejected(self) -> None:
        data = _valid_filter_data(max_amount=Decimal("99.999"))
        with pytest.raises(ValidationError, match="decimal places"):
            CategoryFilterSaveRequest(**data)


class TestFeeCreateRequestDecimalPlaces:
    def _valid_fee_data(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {"year": 2026, "month": 1, "fee": Decimal("15.00")}
        base.update(overrides)
        return base

    def test_two_decimal_places_passes(self) -> None:
        data = self._valid_fee_data(fee=Decimal("15.50"))
        req = FeeCreateRequest(**data)
        assert req.fee == Decimal("15.50")

    def test_three_decimal_places_rejected(self) -> None:
        data = self._valid_fee_data(fee=Decimal("15.505"))
        with pytest.raises(ValidationError, match="decimal places"):
            FeeCreateRequest(**data)


class TestFeeMemberUpdateRequestDecimalPlaces:
    def _valid_data(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "p4x_init_date": date(2020, 1, 1),
            "p4x_init_balance": Decimal("0.00"),
        }
        base.update(overrides)
        return base

    def test_two_decimal_places_passes(self) -> None:
        data = self._valid_data(p4x_init_balance=Decimal("-42.50"))
        req = FeeMemberUpdateRequest(**data)
        assert req.p4x_init_balance == Decimal("-42.50")

    def test_three_decimal_places_rejected(self) -> None:
        data = self._valid_data(p4x_init_balance=Decimal("12.345"))
        with pytest.raises(ValidationError, match="decimal places"):
            FeeMemberUpdateRequest(**data)


# ---------------------------------------------------------------------------
# Anchoring: a trailing line break or inner tab never passes a format check
# ---------------------------------------------------------------------------


class TestFormatChecksRejectLineBreaks:
    @pytest.mark.parametrize("color", ["#fff\n", "#ffffff\n", "\n#fff", "#ff\tf"])
    def test_category_colour(self, color: str) -> None:
        with pytest.raises(ValidationError, match="Farbformat"):
            CategorySaveRequest(**_valid_category_data(background_color=color))
        with pytest.raises(ValidationError, match="Farbformat"):
            CategorySaveRequest(**_valid_category_data(text_color=color))

    @pytest.mark.parametrize("bic", ["GIBAATWW\n", "ABCDEFGH\n", "GIBA ATWW"])
    def test_account_bic(self, bic: str) -> None:
        with pytest.raises(ValidationError, match="BIC"):
            AccountSaveRequest(**_valid_account_data(bic=bic))

    @pytest.mark.parametrize(
        "iban",
        [
            "AT611904300234573201\n",
            "AT61\t1904300234573201",
            "AT61\n1904300234573201",
            "AT61 1904 3002 3457 3201\n",
        ],
    )
    def test_account_iban(self, iban: str) -> None:
        with pytest.raises(ValidationError, match="IBAN"):
            AccountSaveRequest(**_valid_account_data(iban=iban))

    @pytest.mark.parametrize(
        "iban", ["AT61 1904 3002 3457 3201", "AT611904300234573201"]
    )
    def test_account_iban_with_plain_spaces_is_accepted(self, iban: str) -> None:
        assert AccountSaveRequest(**_valid_account_data(iban=iban)).iban == iban

    @pytest.mark.parametrize(
        "iban",
        [
            "AT611904300234573201\n",
            "AT61\t1904300234573201",
            "AT61 1904 3002 3457 3201\n",
        ],
    )
    def test_filter_iban(self, iban: str) -> None:
        with pytest.raises(ValidationError, match="IBAN"):
            CategoryFilterSaveRequest(**_valid_filter_data(iban=iban))

    @pytest.mark.parametrize(
        ("iban", "accepted"),
        [
            ("AT" + "1" * 17, False),
            ("AT" + "1" * 18, True),
            ("AT" + "1" * 23, True),
            ("AT" + "1" * 24, False),
            ("AT" + "1 " * 9 + "1" * 5, True),
        ],
    )
    def test_filter_iban_length_boundaries(self, iban: str, accepted: bool) -> None:
        if accepted:
            assert (
                CategoryFilterSaveRequest(**_valid_filter_data(iban=iban)).iban == iban
            )
            return
        with pytest.raises(ValidationError, match="IBAN"):
            CategoryFilterSaveRequest(**_valid_filter_data(iban=iban))

    @pytest.mark.parametrize(
        ("iban", "accepted"),
        [("AT61123", False), ("AT611234", True), ("AT61 1234", True)],
    )
    def test_account_iban_minimum_length(self, iban: str, accepted: bool) -> None:
        if accepted:
            assert AccountSaveRequest(**_valid_account_data(iban=iban)).iban == iban
            return
        with pytest.raises(ValidationError, match="IBAN"):
            AccountSaveRequest(**_valid_account_data(iban=iban))

    @pytest.mark.parametrize("iban", [None, "", "AT611904300234573201"])
    def test_filter_iban_accepted(self, iban: str | None) -> None:
        assert CategoryFilterSaveRequest(**_valid_filter_data(iban=iban)).iban == iban

    @pytest.mark.parametrize("value", [5, ["AT61"], True])
    def test_filter_iban_of_the_wrong_type_is_a_validation_error(
        self, value: object
    ) -> None:
        with pytest.raises(ValidationError):
            CategoryFilterSaveRequest(**_valid_filter_data(iban=value))


# ---------------------------------------------------------------------------
# Fee year and fee start date stay inside a sane range
# ---------------------------------------------------------------------------


class TestFeeRanges:
    @pytest.mark.parametrize("year", [2015, 2100])
    def test_fee_year_inside_range(self, year: int) -> None:
        req = FeeCreateRequest(year=year, month=1, fee=Decimal("15.00"))
        assert req.year == year

    @pytest.mark.parametrize("year", [2014, 2101, 10000])
    def test_fee_year_outside_range(self, year: int) -> None:
        with pytest.raises(ValidationError):
            FeeCreateRequest(year=year, month=1, fee=Decimal("15.00"))

    @pytest.mark.parametrize("start", [date(1900, 1, 1), date(2020, 6, 15)])
    def test_fee_start_date_inside_range(self, start: date) -> None:
        req = FeeMemberUpdateRequest(
            p4x_init_date=start, p4x_init_balance=Decimal("0.00")
        )
        assert req.p4x_init_date == start

    @pytest.mark.parametrize("start", [date(1899, 12, 31), date(1, 1, 1)])
    def test_fee_start_date_before_1900_is_rejected(self, start: date) -> None:
        with pytest.raises(ValidationError):
            FeeMemberUpdateRequest(
                p4x_init_date=start, p4x_init_balance=Decimal("0.00")
            )


# ---------------------------------------------------------------------------
# Partner references in the set-partner request are strict input
# ---------------------------------------------------------------------------


class TestSetPartnerRequestInput:
    def test_payload_of_the_partner_widget_is_accepted(self) -> None:
        partner_id = uuid.uuid4()
        req = SetPartnerRequest.model_validate(
            {
                "partner": {"type": "member", "id": str(partner_id), "cn": "Muster"},
                "hasDelegatingPartner": True,
                "delegatingPartner": {"type": "contact", "id": str(uuid.uuid4())},
            }
        )
        assert req.partner is not None
        assert (req.partner.type, req.partner.id) == ("member", partner_id)
        assert req.delegatingPartner is not None
        assert req.delegatingPartner.type == "contact"

    @pytest.mark.parametrize(
        "partner_type", ["member", "contact", "account", "special"]
    )
    def test_known_partner_types(self, partner_type: str) -> None:
        req = SetPartnerRequest.model_validate(
            {"partner": {"type": partner_type, "id": str(uuid.uuid4())}}
        )
        assert req.partner is not None
        assert req.partner.type == partner_type

    def test_partner_may_be_cleared(self) -> None:
        assert SetPartnerRequest.model_validate({"partner": None}).partner is None

    @pytest.mark.parametrize("field", ["partner", "delegatingPartner"])
    def test_unknown_partner_type_is_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError):
            SetPartnerRequest.model_validate(
                {field: {"type": "x", "id": str(uuid.uuid4())}}
            )

    @pytest.mark.parametrize("field", ["partner", "delegatingPartner"])
    def test_unknown_key_is_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError, match="evil"):
            SetPartnerRequest.model_validate(
                {field: {"type": "member", "id": str(uuid.uuid4()), "evil": 1}}
            )

    def test_id_must_be_a_uuid(self) -> None:
        with pytest.raises(ValidationError):
            SetPartnerRequest.model_validate({"partner": {"type": "member", "id": "7"}})

    def test_id_is_required(self) -> None:
        with pytest.raises(ValidationError):
            SetPartnerRequest.model_validate({"partner": {"type": "member"}})
