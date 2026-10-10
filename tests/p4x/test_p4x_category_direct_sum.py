"""The direct category assignments of a transaction are either empty or add up to
its amount. The database checks this when the transaction commits, so a
replacement may pass through states that do not add up in between.

The per-test transaction is never committed, so the deferred check is fired by
hand with `assert_direct_sums_hold` (SET CONSTRAINTS ... IMMEDIATE), which runs
every check still pending."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_transaction import P4xTransaction
from app.services import p4x_import_service
from app.services.p4x_category_service import set_category_direct, unset_category_direct
from app.services.p4x_import_service import GEORGE_BIC, parse_george_json
from tests.conftest import TestingSessionLocal

CONSTRAINTS = "p4x_category_directs_sum, p4x_transactions_direct_sum"


def assert_direct_sums_hold(db) -> None:
    """Run the deferred sum checks now; raises IntegrityError when one fails."""
    db.flush()
    db.execute(text(f"SET CONSTRAINTS {CONSTRAINTS} IMMEDIATE"))
    db.execute(text(f"SET CONSTRAINTS {CONSTRAINTS} DEFERRED"))


def _now() -> datetime:
    return datetime.now(UTC)


def _make_account(db) -> P4xAccount:
    account = P4xAccount(
        iban="AT00SUMTEST",
        bic="GIBAATWWXXX",
        label="Sum",
        init_date=date(2020, 1, 1),
        init_balance=0,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(account)
    db.flush()
    return account


def _make_category(db, name: str = "sum.cat") -> P4xCategory:
    category = P4xCategory(
        name=name,
        label=name,
        background_color="#000",
        text_color="#fff",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(category)
    db.flush()
    return category


def _make_transaction(db, account, amount: str = "100.00", suffix: str = "a"):
    tx = P4xTransaction(
        sha256_hash=f"sum_test_{suffix}",
        booking=date(2026, 3, 15),
        valuation=date(2026, 3, 15),
        iban="AT001",
        amount=Decimal(amount),
        subject="Summe",
        p4x_account_id=account.id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(tx)
    db.flush()
    return tx


def _make_direct(db, tx, category, amount: str, **columns) -> P4xCategoryDirect:
    direct = P4xCategoryDirect(
        p4x_transaction_id=tx.id,
        p4x_category_id=category.id,
        amount=Decimal(amount),
        **columns,
    )
    db.add(direct)
    db.flush()
    return direct


@pytest.fixture
def setup(db_session):
    account = _make_account(db_session)
    first = _make_category(db_session, "sum.first")
    second = _make_category(db_session, "sum.second")
    tx = _make_transaction(db_session, account)
    return account, first, second, tx


class TestSumRule:
    def test_one_row_with_the_full_amount_is_accepted(self, db_session, setup):
        _account, first, _second, tx = setup
        _make_direct(db_session, tx, first, "100.00")

        assert_direct_sums_hold(db_session)

    def test_a_split_that_adds_up_is_accepted(self, db_session, setup):
        _account, first, second, tx = setup
        _make_direct(db_session, tx, first, "60.00")
        _make_direct(db_session, tx, second, "40.00")

        assert_direct_sums_hold(db_session)

    def test_cents_are_added_exactly(self, db_session, setup):
        _account, first, second, tx = setup
        _make_direct(db_session, tx, first, "33.33")
        _make_direct(db_session, tx, second, "66.67")

        assert_direct_sums_hold(db_session)

    def test_one_cent_missing_is_refused(self, db_session, setup):
        _account, first, second, tx = setup
        _make_direct(db_session, tx, first, "33.33")
        _make_direct(db_session, tx, second, "66.66")

        with pytest.raises(IntegrityError, match="add up"):
            assert_direct_sums_hold(db_session)

    def test_a_row_with_another_amount_is_refused(self, db_session, setup):
        _account, first, _second, tx = setup
        _make_direct(db_session, tx, first, "99.00")

        with pytest.raises(IntegrityError, match="add up"):
            assert_direct_sums_hold(db_session)

    def test_a_split_that_is_not_complete_is_refused(self, db_session, setup):
        _account, first, _second, tx = setup
        _make_direct(db_session, tx, first, "60.00")

        with pytest.raises(IntegrityError, match="add up"):
            assert_direct_sums_hold(db_session)

    def test_a_transaction_without_rows_is_accepted(self, db_session, setup):
        assert_direct_sums_hold(db_session)

    def test_set_aside_rows_do_not_count(self, db_session, setup):
        _account, first, second, tx = setup
        _make_direct(db_session, tx, first, "100.00")
        _make_direct(db_session, tx, second, "50.00", deleted_at=_now())

        assert_direct_sums_hold(db_session)


class TestChangesAreChecked:
    def test_setting_one_row_of_a_split_aside_is_refused(self, db_session, setup):
        _account, first, second, tx = setup
        _make_direct(db_session, tx, first, "60.00")
        gone = _make_direct(db_session, tx, second, "40.00")
        assert_direct_sums_hold(db_session)

        gone.deleted_at = _now()
        db_session.flush()

        with pytest.raises(IntegrityError, match="add up"):
            assert_direct_sums_hold(db_session)

    def test_changing_the_amount_of_a_row_is_refused(self, db_session, setup):
        _account, first, _second, tx = setup
        row = _make_direct(db_session, tx, first, "100.00")
        assert_direct_sums_hold(db_session)

        row.amount = Decimal("90.00")
        db_session.flush()

        with pytest.raises(IntegrityError, match="add up"):
            assert_direct_sums_hold(db_session)

    def test_bringing_back_a_set_aside_row_is_checked(self, db_session, setup):
        _account, first, second, tx = setup
        _make_direct(db_session, tx, first, "100.00")
        old = _make_direct(db_session, tx, second, "50.00", deleted_at=_now())
        assert_direct_sums_hold(db_session)

        old.deleted_at = None
        db_session.flush()

        with pytest.raises(IntegrityError, match="add up"):
            assert_direct_sums_hold(db_session)

    def test_changing_the_amount_of_a_transaction_with_rows_is_refused(
        self, db_session, setup
    ):
        _account, first, _second, tx = setup
        _make_direct(db_session, tx, first, "100.00")
        assert_direct_sums_hold(db_session)

        tx.amount = Decimal("120.00")
        db_session.flush()

        with pytest.raises(IntegrityError, match="add up"):
            assert_direct_sums_hold(db_session)

    def test_changing_the_amount_of_a_transaction_without_rows_is_accepted(
        self, db_session, setup
    ):
        _account, _first, _second, tx = setup
        tx.amount = Decimal("120.00")
        db_session.flush()

        assert_direct_sums_hold(db_session)

    def test_moving_a_row_to_another_transaction_checks_the_target(
        self, db_session, setup
    ):
        account, first, _second, tx = setup
        other = _make_transaction(db_session, account, "50.00", "b")
        row = _make_direct(db_session, tx, first, "100.00")
        assert_direct_sums_hold(db_session)

        row.p4x_transaction_id = other.id
        db_session.flush()

        with pytest.raises(IntegrityError, match="add up"):
            assert_direct_sums_hold(db_session)

    def test_moving_a_row_away_checks_the_transaction_it_leaves(
        self, db_session, setup
    ):
        account, first, second, tx = setup
        other = _make_transaction(db_session, account, "40.00", "b")
        _make_direct(db_session, tx, first, "60.00")
        moving = _make_direct(db_session, tx, second, "40.00")
        assert_direct_sums_hold(db_session)

        moving.p4x_transaction_id = other.id
        db_session.flush()

        with pytest.raises(IntegrityError, match=str(tx.id)):
            assert_direct_sums_hold(db_session)

    def test_deleting_one_row_of_a_split_is_refused(self, db_session, setup):
        _account, first, second, tx = setup
        _make_direct(db_session, tx, first, "60.00")
        gone = _make_direct(db_session, tx, second, "40.00")
        assert_direct_sums_hold(db_session)

        db_session.delete(gone)
        db_session.flush()

        with pytest.raises(IntegrityError, match="add up"):
            assert_direct_sums_hold(db_session)

    def test_deleting_all_rows_is_accepted(self, db_session, setup):
        _account, first, _second, tx = setup
        row = _make_direct(db_session, tx, first, "100.00")
        assert_direct_sums_hold(db_session)

        db_session.delete(row)
        db_session.flush()

        assert_direct_sums_hold(db_session)

    def test_deleting_the_transaction_with_its_rows_is_accepted(
        self, db_session, setup
    ):
        _account, first, second, tx = setup
        _make_direct(db_session, tx, first, "60.00")
        _make_direct(db_session, tx, second, "40.00")
        assert_direct_sums_hold(db_session)

        db_session.execute(
            text("DELETE FROM p4x_transactions WHERE id = :id"), {"id": tx.id}
        )

        assert_direct_sums_hold(db_session)


class TestWritePathsKeepTheSums:
    def test_replacing_the_assignment_by_a_split(self, db_session, setup):
        _account, first, second, tx = setup
        assert (
            set_category_direct(
                db_session,
                tx,
                [{"p4x_category_id": first.id, "amount": Decimal("100.00")}],
            )
            is None
        )

        error = set_category_direct(
            db_session,
            tx,
            [
                {"p4x_category_id": first.id, "amount": Decimal("70.00")},
                {"p4x_category_id": second.id, "amount": Decimal("30.00")},
            ],
        )

        assert error is None
        assert_direct_sums_hold(db_session)
        active = db_session.query(P4xCategoryDirect).filter_by(deleted_at=None)
        assert sorted(d.amount for d in active) == [Decimal("30.00"), Decimal("70.00")]

    def test_a_refused_split_leaves_the_rows_as_they_were(self, db_session, setup):
        _account, first, second, tx = setup
        set_category_direct(
            db_session,
            tx,
            [{"p4x_category_id": first.id, "amount": Decimal("100.00")}],
        )

        error = set_category_direct(
            db_session,
            tx,
            [
                {"p4x_category_id": first.id, "amount": Decimal("70.00")},
                {"p4x_category_id": second.id, "amount": Decimal("20.00")},
            ],
        )

        assert error is not None
        assert_direct_sums_hold(db_session)

    def test_removing_the_assignment(self, db_session, setup):
        _account, first, _second, tx = setup
        set_category_direct(
            db_session,
            tx,
            [{"p4x_category_id": first.id, "amount": Decimal("100.00")}],
        )

        unset_category_direct(db_session, tx)

        assert_direct_sums_hold(db_session)

    def test_importing_the_same_entry_again_keeps_the_assignment(self, db_session):
        account = _make_account(db_session)
        category = _make_category(db_session)
        entry = {
            "booking": "2026-02-10T00:00:00.000+0100",
            "valuation": "2026-02-10",
            "partnerAccount": {"iban": "AT611904300234573201"},
            "amount": {"value": "1500", "precision": "2"},
            "reference": "Beitrag",
        }
        raw = json.dumps([entry])
        parsed = parse_george_json(GEORGE_BIC, raw)

        def run():
            return p4x_import_service.import_and_apply_filters(
                db_session, account, parsed.entries, json.loads(raw)
            )

        run()
        tx = db_session.query(P4xTransaction).one()
        set_category_direct(
            db_session, tx, [{"p4x_category_id": category.id, "amount": tx.amount}]
        )

        summary = run()

        assert summary["existing"] == 1
        assert_direct_sums_hold(db_session)


class TestCheckedAtCommit:
    """Real commits on separate connections: the check is deferred to the commit."""

    @staticmethod
    def _committed_fixture():
        with TestingSessionLocal() as db:
            account = _make_account(db)
            first = _make_category(db, "sumcommit.first")
            second = _make_category(db, "sumcommit.second")
            tx = _make_transaction(db, account, "100.00", "commit")
            row = _make_direct(db, tx, first, "100.00")
            db.commit()
            return account.id, first.id, second.id, tx.id, row.id

    @staticmethod
    def _cleanup(ids) -> None:
        account_id, first_id, second_id, tx_id, _row_id = ids
        with TestingSessionLocal() as db:
            db.execute(
                text("DELETE FROM p4x_category_directs WHERE p4x_transaction_id = :t"),
                {"t": tx_id},
            )
            db.execute(text("DELETE FROM p4x_transactions WHERE id = :t"), {"t": tx_id})
            db.execute(
                text("DELETE FROM p4x_categories WHERE id IN (:a, :b)"),
                {"a": first_id, "b": second_id},
            )
            db.execute(
                text("DELETE FROM p4x_accounts WHERE id = :a"), {"a": account_id}
            )
            db.commit()

    def test_a_replacement_in_one_commit_passes_through_open_states(self):
        ids = self._committed_fixture()
        _account_id, first_id, second_id, tx_id, row_id = ids
        try:
            with TestingSessionLocal() as db:
                # between the statements the rows do not add up: only the
                # state at the commit counts
                db.execute(
                    text(
                        "UPDATE p4x_category_directs SET deleted_at = now() "
                        "WHERE id = :id"
                    ),
                    {"id": row_id},
                )
                for category_id, amount in ((first_id, "70.00"), (second_id, "30.00")):
                    db.add(
                        P4xCategoryDirect(
                            p4x_transaction_id=tx_id,
                            p4x_category_id=category_id,
                            amount=Decimal(amount),
                        )
                    )
                    db.flush()
                db.commit()

            with TestingSessionLocal() as db:
                total = db.execute(
                    text(
                        "SELECT sum(amount) FROM p4x_category_directs "
                        "WHERE p4x_transaction_id = :t AND deleted_at IS NULL"
                    ),
                    {"t": tx_id},
                ).scalar_one()
            assert total == Decimal("100.00")
        finally:
            self._cleanup(ids)

    def test_a_commit_that_does_not_add_up_is_refused_and_changes_nothing(self):
        ids = self._committed_fixture()
        _account_id, _first_id, second_id, tx_id, _row_id = ids
        try:
            with TestingSessionLocal() as db:
                db.add(
                    P4xCategoryDirect(
                        p4x_transaction_id=tx_id,
                        p4x_category_id=second_id,
                        amount=Decimal("10.00"),
                    )
                )
                with pytest.raises(IntegrityError, match="add up"):
                    db.commit()

            with TestingSessionLocal() as db:
                count = db.execute(
                    text(
                        "SELECT count(*) FROM p4x_category_directs "
                        "WHERE p4x_transaction_id = :t"
                    ),
                    {"t": tx_id},
                ).scalar_one()
            assert count == 1
        finally:
            self._cleanup(ids)
