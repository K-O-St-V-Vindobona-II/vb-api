import json
from datetime import UTC, date, datetime

from app.models.p4x_account import P4xAccount
from app.models.p4x_transaction import P4xTransaction
from app.services.p4x_service import import_transactions, parse_george_json


def _now() -> datetime:
    return datetime.now(UTC)


def _create_account(db, init_date: date = date(2015, 1, 1)) -> P4xAccount:
    account = P4xAccount(
        iban="AT94 2011 1000 0530 1947",
        bic="GIBAATWWXXX",
        label="Girokonto",
        init_date=init_date,
        init_balance=0.0,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _make_george_entry(
    booking: str = "2026-03-20T00:00:00.000+0100",
    amount_value: int = 1500,
    reference: str = "monatlicher MB",
    iban: str = "DE49100110012624770917",
) -> dict:
    return {
        "booking": booking,
        "valuation": booking,
        "partnerAccount": {"iban": iban},
        "amount": {"value": amount_value, "precision": 2},
        "reference": reference,
        "receiverReference": "",
    }


class TestImportBasic:
    def test_import_new_transaction(self, db_session):
        account = _create_account(db_session)
        entry = _make_george_entry()
        raw_json = json.dumps([entry])
        parsed = parse_george_json("GIBAATWWXXX", raw_json)
        assert parsed.success

        summary = import_transactions(db_session, account, parsed.entries, [entry])
        assert summary["giventotal"] == 1
        assert summary["new"] == 1

        txs = db_session.query(P4xTransaction).all()
        assert len(txs) == 1
        assert txs[0].amount == 15.0
        assert txs[0].subject == "monatlicher MB"
        assert txs[0].p4x_account_id == account.id

    def test_import_deduplication(self, db_session):
        account = _create_account(db_session)
        entry = _make_george_entry()
        raw_json = json.dumps([entry])
        parsed = parse_george_json("GIBAATWWXXX", raw_json)

        summary1 = import_transactions(db_session, account, parsed.entries, [entry])
        assert summary1["new"] == 1

        parsed2 = parse_george_json("GIBAATWWXXX", raw_json)
        summary2 = import_transactions(db_session, account, parsed2.entries, [entry])
        assert summary2["existing"] == 1
        assert summary2.get("new", 0) == 0

        txs = (
            db_session.query(P4xTransaction)
            .filter(
                P4xTransaction.deleted_at.is_(None),
            )
            .all()
        )
        assert len(txs) == 1

    def test_import_zero_skipped(self, db_session):
        account = _create_account(db_session)
        entry = _make_george_entry(amount_value=0)
        raw_json = json.dumps([entry])
        parsed = parse_george_json("GIBAATWWXXX", raw_json)

        summary = import_transactions(db_session, account, parsed.entries, [entry])
        assert summary["zero_skipped"] == 1
        assert summary.get("new", 0) == 0

    def test_import_before_init_date(self, db_session):
        account = _create_account(db_session, init_date=date(2026, 6, 1))
        entry = _make_george_entry(booking="2026-03-20T00:00:00.000+0100")
        raw_json = json.dumps([entry])
        parsed = parse_george_json("GIBAATWWXXX", raw_json)

        summary = import_transactions(db_session, account, parsed.entries, [entry])
        assert summary["before_init_date"] == 1
        assert summary.get("new", 0) == 0

    def test_import_multiple_entries(self, db_session):
        account = _create_account(db_session)
        entries = [
            _make_george_entry(reference="MB 1", iban="AT001"),
            _make_george_entry(reference="MB 2", iban="AT002"),
            _make_george_entry(reference="MB 3", amount_value=0),
        ]
        raw_json = json.dumps(entries)
        parsed = parse_george_json("GIBAATWWXXX", raw_json)

        summary = import_transactions(db_session, account, parsed.entries, entries)
        assert summary["giventotal"] == 3
        assert summary["new"] == 2
        assert summary["zero_skipped"] == 1


class TestImportAccountRebinding:
    def test_rebinding_to_different_account(self, db_session):
        account1 = _create_account(db_session)
        entry = _make_george_entry()
        raw_json = json.dumps([entry])
        parsed = parse_george_json("GIBAATWWXXX", raw_json)
        import_transactions(db_session, account1, parsed.entries, [entry])

        account2 = P4xAccount(
            iban="AT00 OTHER",
            bic="GIBAATWWXXX",
            label="Anderes Konto",
            init_date=date(2015, 1, 1),
            init_balance=0.0,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(account2)
        db_session.commit()
        db_session.refresh(account2)

        parsed2 = parse_george_json("GIBAATWWXXX", raw_json)
        summary = import_transactions(db_session, account2, parsed2.entries, [entry])
        assert summary.get("existing_with_new_binding", 0) == 1

        tx = (
            db_session.query(P4xTransaction)
            .filter(
                P4xTransaction.deleted_at.is_(None),
            )
            .first()
        )
        assert tx.p4x_account_id == account2.id


class TestImportEdgeCases:
    def test_import_malformed_payload_missing_fields(self, db_session):
        """import_transactions with entries missing mandatory payload fields."""
        account = _create_account(db_session)

        # Simulate a parsed entry that's missing 'subject'
        bad_entries = [
            {
                "payload": {
                    "booking": date(2026, 3, 20),
                    "valuation": date(2026, 3, 20),
                    "iban": "DE001",
                    "amount": "15.00",
                    # 'subject' is missing
                },
                "raw": "{}",
            }
        ]
        summary = import_transactions(
            db_session, account, bad_entries, [{"booking": "2026-03-20"}]
        )
        assert summary["giventotal"] == 1
        assert summary["error"] == 1
        assert summary.get("new", 0) == 0
