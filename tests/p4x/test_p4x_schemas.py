"""Pure unit tests for Pydantic validators in app/schemas/p4x.py."""

import re
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.p4x import (
    AccountSaveRequest,
    CategoryFilterSaveRequest,
    CategorySaveRequest,
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
        "p4x_account_id": 1,
        "subject_mode": "contains",
        "p4x_category_id": 1,
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
        future = datetime.now(UTC).date() + timedelta(days=30)
        data = _valid_account_data(init_date=future)
        with pytest.raises(ValidationError, match="Zukunft"):
            AccountSaveRequest(**data)

    def test_today_passes(self) -> None:
        data = _valid_account_data(init_date=datetime.now(UTC).date())
        a = AccountSaveRequest(**data)
        assert a.init_date == datetime.now(UTC).date()


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
        future = datetime.now(UTC).date() + timedelta(days=30)
        data = _valid_summary_data(end=future)
        with pytest.raises(ValidationError, match="Zukunft"):
            SummaryOrderRequest(**data)

    def test_end_today_passes(self) -> None:
        data = _valid_summary_data(end=datetime.now(UTC).date())
        s = SummaryOrderRequest(**data)
        assert s.end == datetime.now(UTC).date()


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
