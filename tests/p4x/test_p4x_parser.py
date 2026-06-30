import json
from datetime import date

from app.services.p4x_service import (
    _date_to_carbon_json,
    _php_json_encode,
    compute_transaction_hash,
    parse_george_json,
)

SAMPLE_ENTRY = {
    "transactionId": None,
    "booking": "2026-06-23T00:00:00.000+0200",
    "valuation": "2026-06-23T00:00:00.000+0200",
    "partnerAccount": {"iban": "AT761200023423416700", "bic": "BKAUATWWXXX"},
    "amount": {"value": 3000, "precision": 2, "currency": "EUR"},
    "reference": "MITGLIEDSBEITRAG",
    "receiverReference": "",
}


def _make_json(entries: list[dict]) -> str:
    return json.dumps(entries)


class TestParserValidation:
    def test_unsupported_bic(self):
        result = parse_george_json("UNKNOWN", "[]")
        assert not result.success
        assert "No parser method found for BIC UNKNOWN" in result.message

    def test_invalid_json(self):
        result = parse_george_json("GIBAATWWXXX", "not json")
        assert not result.success
        assert "failed to parse" in result.message

    def test_not_array(self):
        result = parse_george_json("GIBAATWWXXX", '{"key": "value"}')
        assert not result.success
        assert "failed to parse" in result.message

    def test_missing_booking(self):
        entry = {**SAMPLE_ENTRY}
        del entry["booking"]
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert not result.success
        assert "missing field: booking" in result.message

    def test_missing_valuation(self):
        entry = {**SAMPLE_ENTRY}
        del entry["valuation"]
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert not result.success
        assert "missing field: valuation" in result.message

    def test_missing_partnerAccount(self):
        entry = {**SAMPLE_ENTRY}
        del entry["partnerAccount"]
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert not result.success
        assert "missing field: partnerAccount" in result.message

    def test_partnerAccount_not_dict_gets_default_iban(self):
        entry = {**SAMPLE_ENTRY, "partnerAccount": "not a dict"}
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert result.success
        assert result.entries[0]["payload"]["iban"] == ""

    def test_missing_partnerAccount_iban(self):
        entry = {**SAMPLE_ENTRY, "partnerAccount": {"bic": "test"}}
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert not result.success
        assert "missing field: partnerAccount.iban" in result.message

    def test_missing_amount(self):
        entry = {**SAMPLE_ENTRY}
        del entry["amount"]
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert not result.success
        assert "missing field: amount" in result.message

    def test_missing_amount_value(self):
        entry = {**SAMPLE_ENTRY, "amount": {"precision": 2}}
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert not result.success
        assert "missing field: amount.value" in result.message

    def test_missing_amount_precision(self):
        entry = {**SAMPLE_ENTRY, "amount": {"value": 100}}
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert not result.success
        assert "missing field: amount.precision" in result.message

    def test_missing_both_references(self):
        entry = {**SAMPLE_ENTRY}
        del entry["reference"]
        del entry["receiverReference"]
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert not result.success
        assert "missing field: reference or receiverReference" in result.message

    def test_empty_array(self):
        result = parse_george_json("GIBAATWWXXX", "[]")
        assert result.success
        assert result.entries == []


class TestParserTransformation:
    def test_basic_entry(self):
        result = parse_george_json("GIBAATWWXXX", _make_json([SAMPLE_ENTRY]))
        assert result.success
        payload = result.entries[0]["payload"]
        assert payload["booking"] == date(2026, 6, 23)
        assert payload["valuation"] == date(2026, 6, 23)
        assert payload["iban"] == "AT761200023423416700"
        assert payload["amount"] == "30.00"
        assert payload["subject"] == "MITGLIEDSBEITRAG"

    def test_amount_precision(self):
        entry = {**SAMPLE_ENTRY, "amount": {"value": 123456, "precision": 2}}
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert result.entries[0]["payload"]["amount"] == "1234.56"

    def test_amount_negative(self):
        entry = {**SAMPLE_ENTRY, "amount": {"value": -6223, "precision": 2}}
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert result.entries[0]["payload"]["amount"] == "-62.23"

    def test_amount_zero(self):
        entry = {**SAMPLE_ENTRY, "amount": {"value": 0, "precision": 2}}
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert result.entries[0]["payload"]["amount"] == "0.00"

    def test_subject_reference_only(self):
        entry = {**SAMPLE_ENTRY, "reference": "Ref Text", "receiverReference": ""}
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert result.entries[0]["payload"]["subject"] == "Ref Text"

    def test_subject_receiverReference_only(self):
        entry = {**SAMPLE_ENTRY, "reference": "", "receiverReference": "Recv Text"}
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert result.entries[0]["payload"]["subject"] == "Recv Text"

    def test_subject_both_picks_longer(self):
        entry = {
            **SAMPLE_ENTRY,
            "reference": "Short",
            "receiverReference": "Longer text here",
        }
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert result.entries[0]["payload"]["subject"] == "Longer text here"

    def test_subject_equal_length_picks_receiverReference(self):
        entry = {
            **SAMPLE_ENTRY,
            "reference": "ABCDE",
            "receiverReference": "FGHIJ",
        }
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert result.entries[0]["payload"]["subject"] == "FGHIJ"

    def test_subject_both_empty(self):
        entry = {**SAMPLE_ENTRY, "reference": "", "receiverReference": ""}
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert result.entries[0]["payload"]["subject"] == ""

    def test_empty_iban_allowed(self):
        entry = {**SAMPLE_ENTRY, "partnerAccount": {"iban": ""}}
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert result.success
        assert result.entries[0]["payload"]["iban"] == ""

    def test_raw_is_re_encoded(self):
        result = parse_george_json("GIBAATWWXXX", _make_json([SAMPLE_ENTRY]))
        raw_parsed = json.loads(result.entries[0]["raw"])
        assert raw_parsed["booking"] == SAMPLE_ENTRY["booking"]

    def test_date_parsing_with_timezone(self):
        entry = {**SAMPLE_ENTRY, "booking": "  2026-01-15T00:00:00.000+0100  "}
        result = parse_george_json("GIBAATWWXXX", _make_json([entry]))
        assert result.entries[0]["payload"]["booking"] == date(2026, 1, 15)

    def test_multiple_entries(self):
        result = parse_george_json(
            "GIBAATWWXXX",
            _make_json([SAMPLE_ENTRY, SAMPLE_ENTRY]),
        )
        assert result.success
        assert len(result.entries) == 2


class TestCarbonJsonFormat:
    def test_cet_winter(self):
        result = _date_to_carbon_json(
            date(2015, 1, 2),
            "2015-01-02T00:00:00.000+0100",
        )
        assert result == "2015-01-01T23:00:00.000000Z"

    def test_cest_summer(self):
        result = _date_to_carbon_json(
            date(2026, 6, 23),
            "2026-06-23T00:00:00.000+0200",
        )
        assert result == "2026-06-22T22:00:00.000000Z"

    def test_utc(self):
        result = _date_to_carbon_json(
            date(2026, 1, 1),
            "2026-01-01T00:00:00.000+0000",
        )
        assert result == "2026-01-01T00:00:00.000000Z"

    def test_fallback_no_timezone(self):
        result = _date_to_carbon_json(date(2026, 1, 1), "2026-01-01")
        assert result == "2026-01-01T00:00:00.000000Z"


class TestPhpJsonEncode:
    def test_escapes_unicode(self):
        result = _php_json_encode(["ü"])
        assert "\\u00fc" in result

    def test_escapes_slash(self):
        result = _php_json_encode(["a/b"])
        assert "a\\/b" in result

    def test_compact_format(self):
        result = _php_json_encode([1, 2, 3])
        assert result == "[1,2,3]"


class TestSha256Hash:
    def test_known_hash(self):
        """Verified against PHP: first transaction in legacy DB."""
        booking_carbon = "2015-01-01T23:00:00.000000Z"
        valuation_carbon = "2015-01-01T23:00:00.000000Z"
        iban = "AT342011100032605714"
        amount = "15.00"
        subject = "MITGLIEDSBEITRAG Dr. Günter Rußegger 1040 Johann Straussg 39/7"

        computed = compute_transaction_hash(
            booking_carbon,
            valuation_carbon,
            iban,
            amount,
            subject,
        )
        assert (
            computed
            == "d85b9ed3cdb6e637a64e6990c2471080c4a4d5076d0a80e5b3e5f12706681e78"
        )
