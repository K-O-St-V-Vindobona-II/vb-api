from datetime import UTC, date, datetime

from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
from app.models.p4x_partner import P4xPartner
from app.models.p4x_transaction import P4xTransaction
from app.services.p4x_service import (
    get_account_balance,
    get_account_categories,
    get_transactions_by_category,
    get_transactions_by_filter,
    get_transactions_by_month,
    get_transactions_by_partner,
    get_warnings_category,
    get_warnings_partner,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _create_account(db, init_balance: float = 0.0) -> P4xAccount:
    account = P4xAccount(
        iban="AT942011100005301947",
        bic="GIBAATWWXXX",
        label="Girokonto",
        init_date=date(2015, 1, 1),
        init_balance=init_balance,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _add_tx(
    db,
    account: P4xAccount,
    booking: date,
    amount: float,
    iban: str = "AT001",
    subject: str = "Test",
    hash_suffix: str = "",
    delegating_partner_type: str | None = None,
    delegating_partner_id: int | None = None,
) -> P4xTransaction:
    tx = P4xTransaction(
        sha256hash=f"q_{booking}_{amount}_{iban}_{hash_suffix}",
        booking=booking,
        valuation=booking,
        iban=iban,
        amount=amount,
        subject=subject,
        p4x_account_id=account.id,
        delegating_partner_type=delegating_partner_type,
        delegating_partner_id=delegating_partner_id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


class TestGetAccountBalanceDefault:
    def test_balance_without_up_to_date(self, db_session):
        """get_account_balance with no up_to_date defaults to today."""
        account = _create_account(db_session, init_balance=100.0)
        _add_tx(db_session, account, date(2020, 1, 1), 50.0)

        balance = get_account_balance(db_session, account)
        assert balance == 150.0


class TestGetTransactionsByMonth:
    def test_returns_transactions_for_given_month(self, db_session):
        account = _create_account(db_session)
        _add_tx(db_session, account, date(2026, 3, 10), 10.0, hash_suffix="a")
        _add_tx(db_session, account, date(2026, 3, 20), 20.0, hash_suffix="b")
        _add_tx(db_session, account, date(2026, 4, 5), 30.0, hash_suffix="c")

        items, total = get_transactions_by_month(db_session, account, 2026, 3, 1)
        assert total == 2
        assert len(items) == 2
        assert all(item.booking.month == 3 for item in items)

    def test_empty_month(self, db_session):
        account = _create_account(db_session)
        _add_tx(db_session, account, date(2026, 3, 10), 10.0)

        items, total = get_transactions_by_month(db_session, account, 2026, 5, 1)
        assert total == 0
        assert items == []

    def test_pagination(self, db_session):
        account = _create_account(db_session)
        _add_tx(db_session, account, date(2026, 3, 10), 10.0, hash_suffix="p1")
        _add_tx(db_session, account, date(2026, 3, 11), 20.0, hash_suffix="p2")

        items_p1, total = get_transactions_by_month(db_session, account, 2026, 3, 1)
        assert total == 2
        assert len(items_p1) == 2

        # Page 2 empty since only 2 items and PAGINATION_SIZE is 100
        items_p2, total_p2 = get_transactions_by_month(db_session, account, 2026, 3, 2)
        assert total_p2 == 2
        assert items_p2 == []


class TestGetTransactionsByPartner:
    def test_returns_transactions_by_partner_iban(self, db_session):
        account = _create_account(db_session)
        partner = P4xPartner(
            iban="DE001",
            partner_type="member",
            partner_id=42,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(partner)
        db_session.commit()

        _add_tx(db_session, account, date(2026, 3, 10), 15.0, iban="DE001")
        _add_tx(db_session, account, date(2026, 3, 11), 25.0, iban="AT999")

        items, total = get_transactions_by_partner(db_session, account, "member", 42, 1)
        assert total == 1
        assert items[0].iban == "DE001"

    def test_returns_transactions_by_delegating_partner(self, db_session):
        account = _create_account(db_session)
        _add_tx(
            db_session,
            account,
            date(2026, 3, 10),
            15.0,
            iban="AT999",
            delegating_partner_type="member",
            delegating_partner_id=42,
        )

        items, total = get_transactions_by_partner(db_session, account, "member", 42, 1)
        assert total == 1
        assert items[0].delegating_partner_id == 42

    def test_empty_when_no_partner_match(self, db_session):
        account = _create_account(db_session)
        _add_tx(db_session, account, date(2026, 3, 10), 15.0)

        items, total = get_transactions_by_partner(
            db_session, account, "member", 999, 1
        )
        assert total == 0
        assert items == []


class TestGetTransactionsByCategory:
    def test_returns_direct_category_transactions(self, db_session):
        account = _create_account(db_session)
        cat = P4xCategory(
            name="test.cat",
            label="TestCat",
            background_color="#000",
            text_color="#fff",
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cat)
        db_session.commit()
        db_session.refresh(cat)

        tx = _add_tx(db_session, account, date(2026, 3, 10), 15.0)
        db_session.add(
            P4xCategoryDirect(
                p4x_transaction_id=tx.id,
                p4x_category_id=cat.id,
                amount=15.0,
            )
        )
        db_session.commit()

        items, total = get_transactions_by_category(db_session, account, cat.id, 1)
        assert total == 1
        assert items[0].id == tx.id

    def test_returns_filter_hit_transactions_minus_directs(self, db_session):
        account = _create_account(db_session)
        cat = P4xCategory(
            name="test.cat",
            label="TestCat",
            background_color="#000",
            text_color="#fff",
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cat)
        db_session.commit()
        db_session.refresh(cat)

        cf = P4xCategoryFilter(
            name="f1",
            p4x_account_id=account.id,
            subject_mode="equals",
            subject="FilterHit",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cf)
        db_session.commit()
        db_session.refresh(cf)

        tx = _add_tx(db_session, account, date(2026, 3, 10), 15.0, subject="FilterHit")
        db_session.add(
            P4xCategoryFilterHit(
                p4x_transaction_id=tx.id,
                p4x_category_filter_id=cf.id,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        db_session.commit()

        items, total = get_transactions_by_category(db_session, account, cat.id, 1)
        assert total == 1
        assert items[0].id == tx.id

    def test_empty_when_no_category_match(self, db_session):
        account = _create_account(db_session)
        items, total = get_transactions_by_category(db_session, account, 999, 1)
        assert total == 0
        assert items == []


class TestGetTransactionsByFilter:
    def test_returns_filter_hit_transactions(self, db_session):
        account = _create_account(db_session)
        cat = P4xCategory(
            name="test.cat",
            label="TestCat",
            background_color="#000",
            text_color="#fff",
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cat)
        db_session.commit()
        db_session.refresh(cat)

        cf = P4xCategoryFilter(
            name="f1",
            p4x_account_id=account.id,
            subject_mode="equals",
            subject="X",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cf)
        db_session.commit()
        db_session.refresh(cf)

        tx = _add_tx(db_session, account, date(2026, 3, 10), 15.0, subject="X")
        db_session.add(
            P4xCategoryFilterHit(
                p4x_transaction_id=tx.id,
                p4x_category_filter_id=cf.id,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        db_session.commit()

        items, total = get_transactions_by_filter(db_session, account, cf.id, 1)
        assert total == 1
        assert items[0].id == tx.id

    def test_excludes_transactions_with_directs(self, db_session):
        account = _create_account(db_session)
        cat = P4xCategory(
            name="test.cat",
            label="TestCat",
            background_color="#000",
            text_color="#fff",
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cat)
        db_session.commit()
        db_session.refresh(cat)

        cf = P4xCategoryFilter(
            name="f1",
            p4x_account_id=account.id,
            subject_mode="equals",
            subject="X",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cf)
        db_session.commit()
        db_session.refresh(cf)

        tx = _add_tx(db_session, account, date(2026, 3, 10), 15.0, subject="X")

        # Both a filter hit AND a direct -> excluded from filter view
        db_session.add(
            P4xCategoryFilterHit(
                p4x_transaction_id=tx.id,
                p4x_category_filter_id=cf.id,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        db_session.add(
            P4xCategoryDirect(
                p4x_transaction_id=tx.id,
                p4x_category_id=cat.id,
                amount=15.0,
            )
        )
        db_session.commit()

        items, total = get_transactions_by_filter(db_session, account, cf.id, 1)
        assert total == 0
        assert items == []

    def test_empty_when_no_hits(self, db_session):
        account = _create_account(db_session)
        items, total = get_transactions_by_filter(db_session, account, 999, 1)
        assert total == 0
        assert items == []


class TestGetAccountCategories:
    def test_returns_categories_for_account(self, db_session):
        account = _create_account(db_session)
        cat = P4xCategory(
            name="test.cat",
            label="TestCat",
            background_color="#000",
            text_color="#fff",
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cat)
        db_session.commit()
        db_session.refresh(cat)

        tx = _add_tx(db_session, account, date(2026, 3, 10), 15.0)
        db_session.add(
            P4xCategoryDirect(
                p4x_transaction_id=tx.id,
                p4x_category_id=cat.id,
                amount=15.0,
            )
        )
        db_session.commit()

        categories = get_account_categories(db_session, account)
        assert len(categories) == 1
        assert categories[0].id == cat.id

    def test_empty_when_no_transactions(self, db_session):
        account = _create_account(db_session)
        categories = get_account_categories(db_session, account)
        assert categories == []

    def test_includes_filter_hit_categories(self, db_session):
        account = _create_account(db_session)
        cat = P4xCategory(
            name="test.cat",
            label="TestCat",
            background_color="#000",
            text_color="#fff",
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cat)
        db_session.commit()
        db_session.refresh(cat)

        cf = P4xCategoryFilter(
            name="f1",
            p4x_account_id=account.id,
            subject_mode="equals",
            subject="X",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cf)
        db_session.commit()
        db_session.refresh(cf)

        tx = _add_tx(db_session, account, date(2026, 3, 10), 15.0, subject="X")
        db_session.add(
            P4xCategoryFilterHit(
                p4x_transaction_id=tx.id,
                p4x_category_filter_id=cf.id,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        db_session.commit()

        categories = get_account_categories(db_session, account)
        assert len(categories) == 1
        assert categories[0].id == cat.id


class TestGetWarningsWithLimit:
    def test_warnings_partner_with_limit(self, db_session):
        account = _create_account(db_session)
        _add_tx(
            db_session,
            account,
            date(2026, 3, 10),
            10.0,
            iban="UNKNOWN1",
            hash_suffix="w1",
        )
        _add_tx(
            db_session,
            account,
            date(2026, 3, 11),
            20.0,
            iban="UNKNOWN2",
            hash_suffix="w2",
        )
        _add_tx(
            db_session,
            account,
            date(2026, 3, 12),
            30.0,
            iban="UNKNOWN3",
            hash_suffix="w3",
        )

        items, total = get_warnings_partner(db_session, limit=2)
        assert total == 3
        assert len(items) == 2

    def test_warnings_category_with_limit(self, db_session):
        account = _create_account(db_session)
        # Transactions with no category (0 filter hits, no directs) -> warning
        _add_tx(db_session, account, date(2026, 3, 10), 10.0, hash_suffix="wc1")
        _add_tx(db_session, account, date(2026, 3, 11), 20.0, hash_suffix="wc2")
        _add_tx(db_session, account, date(2026, 3, 12), 30.0, hash_suffix="wc3")

        items, total = get_warnings_category(db_session, limit=2)
        assert total == 3
        assert len(items) == 2
