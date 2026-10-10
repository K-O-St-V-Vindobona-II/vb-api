"""Corrupt bank entries are refused with a message instead of an exception or a
poisoned balance."""

import json
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from app.models.p4x_transaction import P4xTransaction
from app.services import p4x_import_service
from app.services.p4x_import_service import parse_george_json
from tests.p4x.test_p4x_parser import SAMPLE_ENTRY
from tests.p4x.test_p4x_service_queries import _create_account

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.p4x_account import P4xAccount

BIC = "GIBAATWWXXX"


def _entry(**changes: object) -> dict[str, object]:
    entry = json.loads(json.dumps(SAMPLE_ENTRY))
    for key, value in changes.items():
        if key.startswith("amount_"):
            entry["amount"][key.removeprefix("amount_")] = value
        else:
            entry[key] = value
    return entry


def _parse(*entries: object):
    return parse_george_json(BIC, json.dumps(list(entries)))


class TestCorruptEntriesAreRefused:
    @pytest.mark.parametrize(
        ("entry", "field"),
        [
            (_entry(amount_value="NaN"), "amount.value"),
            (_entry(amount_value="nan"), "amount.value"),
            (_entry(amount_value="Infinity"), "amount.value"),
            (_entry(amount_value="-Infinity"), "amount.value"),
            (_entry(amount_value="1" + "0" * 30), "amount.value"),
            (_entry(amount_value="abc"), "amount.value"),
            (_entry(amount_value=""), "amount.value"),
            (_entry(amount_value=None), "amount.value"),
            (_entry(amount_precision="999999999"), "amount.precision"),
            (_entry(amount_precision="10"), "amount.precision"),
            (_entry(amount_precision="-1"), "amount.precision"),
            (_entry(amount_precision="x"), "amount.precision"),
            (_entry(amount_precision=None), "amount.precision"),
            (_entry(amount_precision=2.5), "amount.precision"),
            (_entry(booking="not a date"), "booking"),
            (_entry(valuation="2026-13-45"), "valuation"),
            (_entry(booking=None), "booking"),
        ],
    )
    def test_entry_gives_a_failed_parse_naming_the_field(
        self, entry: dict[str, object], field: str
    ) -> None:
        result = _parse(entry)

        assert result.success is False
        assert result.message.startswith(
            "given data contains at least one corrupt entry: "
        )
        assert field in result.message
        assert result.entries == []

    @pytest.mark.parametrize("entry", [5, None, "text", ["x"], True])
    def test_entry_that_is_not_an_object(self, entry: object) -> None:
        result = _parse(entry)

        assert result.success is False
        assert result.message.endswith("entry is not an object")

    @pytest.mark.parametrize("amount", [5, "10", None, ["1"]])
    def test_amount_that_is_not_an_object(self, amount: object) -> None:
        result = _parse(_entry(amount=amount))

        assert result.success is False
        assert "amount" in result.message

    def test_one_corrupt_entry_fails_the_whole_file(self) -> None:
        result = _parse(_entry(), _entry(amount_value="NaN"), _entry())

        assert result.success is False
        assert result.entries == []


class TestAcceptedAmounts:
    @pytest.mark.parametrize(
        ("value", "precision", "expected"),
        [
            ("1500", "2", "15.00"),
            (1500, 2, "15.00"),
            ("15", "0", "15.00"),
            ("1", "9", "0.00"),
            ("-1500", "2", "-15.00"),
            ("999999999999", "2", "9999999999.99"),
            ("-999999999999", "2", "-9999999999.99"),
            (" 1500 ", " 2 ", "15.00"),
            ("1234", "3", "1.23"),
        ],
    )
    def test_amount_is_scaled_by_its_precision(
        self, value: object, precision: object, expected: str
    ) -> None:
        result = _parse(_entry(amount_value=value, amount_precision=precision))

        assert result.success is True
        assert result.entries[0]["payload"]["amount"] == expected

    @pytest.mark.parametrize(
        ("value", "precision"),
        [("1000000000000", "2"), ("-1000000000000", "2"), ("10000000000", "0")],
    )
    def test_amount_of_ten_billion_or_more_is_refused(
        self, value: str, precision: str
    ) -> None:
        result = _parse(_entry(amount_value=value, amount_precision=precision))

        assert result.success is False
        assert "amount.value" in result.message

    def test_decimal_amounts_stay_exact(self) -> None:
        result = _parse(_entry(amount_value="1", amount_precision="2"))

        assert Decimal(result.entries[0]["payload"]["amount"]) == Decimal("0.01")


class TestEntriesThatStillParse:
    def test_normal_entry(self) -> None:
        result = _parse(SAMPLE_ENTRY)

        assert result.success is True
        assert result.message == "finished successfully"
        assert result.entries[0]["payload"]["amount"] == "30.00"
        assert result.entries[0]["payload"]["subject"] == "MITGLIEDSBEITRAG"

    @pytest.mark.parametrize("counter_account", [None, "", 5, []])
    def test_cash_entry_without_a_counter_account_object(
        self, counter_account: object
    ) -> None:
        result = _parse(_entry(partnerAccount=counter_account))

        assert result.success is True
        assert result.entries[0]["payload"]["iban"] == ""

    def test_longer_of_the_two_references_is_the_subject(self) -> None:
        longer = _parse(_entry(reference="kurz", receiverReference="viel laenger"))
        assert longer.entries[0]["payload"]["subject"] == "viel laenger"
        equal = _parse(_entry(reference="gleich", receiverReference="selbst"))
        assert equal.entries[0]["payload"]["subject"] == "selbst"

    @pytest.mark.parametrize(
        ("reference", "receiver", "subject"),
        [
            ("nur ref", "", "nur ref"),
            ("", "nur recv", "nur recv"),
            (None, "nur recv", "nur recv"),
            ("", "", ""),
            (None, None, ""),
            ("  Rand  ", "", "Rand"),
        ],
    )
    def test_subject_from_one_or_no_reference(
        self, reference: object, receiver: object, subject: str
    ) -> None:
        result = _parse(_entry(reference=reference, receiverReference=receiver))

        assert result.entries[0]["payload"]["subject"] == subject

    @pytest.mark.parametrize("missing", ["reference", "receiverReference"])
    def test_one_reference_field_is_enough(self, missing: str) -> None:
        entry = _entry(reference="Text", receiverReference="Text")
        del entry[missing]

        assert _parse(entry).success is True

    def test_without_any_reference_field_the_entry_is_refused(self) -> None:
        entry = _entry()
        del entry["reference"]
        del entry["receiverReference"]

        result = _parse(entry)

        assert result.success is False
        assert result.message.endswith("missing field: reference or receiverReference")

    @pytest.mark.parametrize(
        "missing",
        ["booking", "valuation", "partnerAccount", "amount"],
    )
    def test_missing_field_is_named_exactly(self, missing: str) -> None:
        entry = _entry()
        del entry[missing]

        assert _parse(entry).message.endswith(f"missing field: {missing}")

    def test_the_raw_entry_is_kept(self) -> None:
        result = _parse(SAMPLE_ENTRY)

        assert json.loads(result.entries[0]["raw"]) == SAMPLE_ENTRY


class TestRepeatedEntries:
    """Two payments that agree in every hashed field are two bookings."""

    def _import(
        self, db: Session, account: P4xAccount, *entries: object
    ) -> dict[str, int]:
        raw = json.dumps(list(entries))
        result = parse_george_json(BIC, raw)
        return p4x_import_service.import_and_apply_filters(
            db, account, result.entries, json.loads(raw)
        )

    def test_two_identical_entries_are_two_transactions(
        self, db_session: Session
    ) -> None:
        account = _create_account(db_session)

        summary = self._import(db_session, account, SAMPLE_ENTRY, SAMPLE_ENTRY)

        assert summary["new"] == 2
        assert summary["repeated_in_file"] == 1
        assert db_session.query(P4xTransaction).count() == 2

    def test_every_further_occurrence_gets_its_own_hash(
        self, db_session: Session
    ) -> None:
        account = _create_account(db_session)

        summary = self._import(db_session, account, *[SAMPLE_ENTRY] * 3)

        hashes = {t.sha256_hash for t in db_session.query(P4xTransaction)}
        assert summary["new"] == 3
        assert summary["repeated_in_file"] == 2
        assert len(hashes) == 3

    def test_entries_that_differ_are_not_reported_as_repeated(
        self, db_session: Session
    ) -> None:
        account = _create_account(db_session)

        summary = self._import(
            db_session, account, SAMPLE_ENTRY, _entry(reference="Anderer Zweck")
        )

        assert summary["new"] == 2
        assert "repeated_in_file" not in summary

    def test_reimport_of_the_same_file_adds_nothing(self, db_session: Session) -> None:
        account = _create_account(db_session)
        self._import(db_session, account, SAMPLE_ENTRY, SAMPLE_ENTRY)

        summary = self._import(db_session, account, SAMPLE_ENTRY, SAMPLE_ENTRY)

        assert summary["existing"] == 2
        assert "new" not in summary
        assert db_session.query(P4xTransaction).count() == 2

    def test_first_occurrence_keeps_the_hash_it_had_before(
        self, db_session: Session
    ) -> None:
        account = _create_account(db_session)
        self._import(db_session, account, SAMPLE_ENTRY)
        first_hash = db_session.query(P4xTransaction).one().sha256_hash

        summary = self._import(db_session, account, SAMPLE_ENTRY, SAMPLE_ENTRY)

        hashes = {t.sha256_hash for t in db_session.query(P4xTransaction)}
        assert summary["existing"] == 1
        assert summary["new"] == 1
        assert first_hash in hashes
        assert len(hashes) == 2

    def test_repeated_entries_before_the_start_date_are_counted_too(
        self, db_session: Session
    ) -> None:
        account = _create_account(db_session)
        old = _entry(
            booking="2010-01-04T00:00:00.000+0100",
            valuation="2010-01-04T00:00:00.000+0100",
        )

        summary = self._import(db_session, account, old, old)

        assert summary["before_init_date"] == 2
        assert summary["repeated_in_file"] == 1
        assert db_session.query(P4xTransaction).count() == 0
