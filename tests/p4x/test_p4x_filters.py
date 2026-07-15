from datetime import UTC, date, datetime

from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
from app.models.p4x_transaction import P4xTransaction
from app.services.p4x_service import (
    apply_all_category_filters,
    apply_single_filter,
    get_filter_hit_count,
    get_filter_hits,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _seed_data(db) -> tuple[P4xAccount, P4xCategory, list[P4xTransaction]]:
    account = P4xAccount(
        iban="AT00TEST",
        bic="GIBAATWWXXX",
        label="Test",
        init_date=date(2020, 1, 1),
        init_balance=0,
        created_at=_now(),
        updated_at=_now(),
    )
    cat = P4xCategory(
        name="mitgliedsbeitrag",
        label="MB",
        background_color="#336600",
        text_color="#ffffff",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add_all([account, cat])
    db.commit()
    db.refresh(account)
    db.refresh(cat)

    txs = []
    for i, (subject, amount, iban) in enumerate(
        [
            ("mitgliedsbeitrag Kopernikus", 15.0, "DE001"),
            ("mitgliedsbeitrag Roland", 30.0, "AT001"),
            ("mitgliedsb. Bacchus", 20.0, "AT002"),
            ("Spende Verein", 100.0, "AT003"),
            ("Kontogebühr", -5.0, ""),
        ]
    ):
        tx = P4xTransaction(
            sha256hash=f"filter_test_{i}",
            booking=date(2026, 3, 10 + i),
            valuation=date(2026, 3, 10 + i),
            iban=iban,
            amount=amount,
            subject=subject,
            p4x_account_id=account.id,
            created_at=_now(),
            updated_at=_now(),
        )
        txs.append(tx)
    db.add_all(txs)
    db.commit()
    for tx in txs:
        db.refresh(tx)
    return account, cat, txs


class TestFilterEngineSubjectModes:
    def test_starts_mode(self, db_session):
        account, cat, _txs = _seed_data(db_session)
        f = P4xCategoryFilter(
            name="starts_test",
            p4x_account_id=account.id,
            subject_mode="starts",
            subject="mitgliedsbeitrag",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(f)
        db_session.commit()

        apply_single_filter(db_session, f)
        assert get_filter_hit_count(db_session, f) == 2

    def test_contains_mode(self, db_session):
        account, cat, _txs = _seed_data(db_session)
        f = P4xCategoryFilter(
            name="contains_test",
            p4x_account_id=account.id,
            subject_mode="contains",
            subject="mitgliedsb",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(f)
        db_session.commit()

        apply_single_filter(db_session, f)
        assert get_filter_hit_count(db_session, f) == 3

    def test_equals_mode(self, db_session):
        account, cat, _txs = _seed_data(db_session)
        f = P4xCategoryFilter(
            name="equals_test",
            p4x_account_id=account.id,
            subject_mode="equals",
            subject="Spende Verein",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(f)
        db_session.commit()

        apply_single_filter(db_session, f)
        assert get_filter_hit_count(db_session, f) == 1

    def test_starts_mode_is_case_insensitive(self, db_session):
        """Regression test: Postgres LIKE is case-sensitive, unlike the
        legacy MySQL system's default collation. A filter written in a
        different case than the transaction subject must still match."""
        account, cat, _txs = _seed_data(db_session)
        f = P4xCategoryFilter(
            name="starts_case_test",
            p4x_account_id=account.id,
            subject_mode="starts",
            subject="MITGLIEDSBEITRAG",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(f)
        db_session.commit()

        apply_single_filter(db_session, f)
        assert get_filter_hit_count(db_session, f) == 2

    def test_contains_mode_is_case_insensitive(self, db_session):
        account, cat, _txs = _seed_data(db_session)
        f = P4xCategoryFilter(
            name="contains_case_test",
            p4x_account_id=account.id,
            subject_mode="contains",
            subject="MITGLIEDSB",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(f)
        db_session.commit()

        apply_single_filter(db_session, f)
        assert get_filter_hit_count(db_session, f) == 3

    def test_equals_mode_is_case_insensitive(self, db_session):
        account, cat, _txs = _seed_data(db_session)
        f = P4xCategoryFilter(
            name="equals_case_test",
            p4x_account_id=account.id,
            subject_mode="equals",
            subject="spende verein",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(f)
        db_session.commit()

        apply_single_filter(db_session, f)
        assert get_filter_hit_count(db_session, f) == 1


class TestFilterEngineAmountRange:
    def test_min_amount(self, db_session):
        account, cat, _txs = _seed_data(db_session)
        f = P4xCategoryFilter(
            name="min_test",
            p4x_account_id=account.id,
            subject_mode="equals",
            min_amount=20.0,
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(f)
        db_session.commit()

        apply_single_filter(db_session, f)
        hits = get_filter_hits(db_session, f)
        assert all(tx.amount >= 20.0 for tx in hits)

    def test_max_amount(self, db_session):
        account, cat, _txs = _seed_data(db_session)
        f = P4xCategoryFilter(
            name="max_test",
            p4x_account_id=account.id,
            subject_mode="equals",
            max_amount=20.0,
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(f)
        db_session.commit()

        apply_single_filter(db_session, f)
        hits = get_filter_hits(db_session, f)
        assert all(tx.amount <= 20.0 for tx in hits)

    def test_amount_range(self, db_session):
        account, cat, _txs = _seed_data(db_session)
        f = P4xCategoryFilter(
            name="range_test",
            p4x_account_id=account.id,
            subject_mode="equals",
            min_amount=10.0,
            max_amount=25.0,
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(f)
        db_session.commit()

        apply_single_filter(db_session, f)
        hits = get_filter_hits(db_session, f)
        assert all(10.0 <= tx.amount <= 25.0 for tx in hits)


class TestFilterEngineIban:
    def test_iban_filter(self, db_session):
        account, cat, _txs = _seed_data(db_session)
        f = P4xCategoryFilter(
            name="iban_test",
            p4x_account_id=account.id,
            iban="DE001",
            subject_mode="equals",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(f)
        db_session.commit()

        apply_single_filter(db_session, f)
        assert get_filter_hit_count(db_session, f) == 1
        hits = get_filter_hits(db_session, f)
        assert hits[0].iban == "DE001"


class TestFilterEngineDirectExclusion:
    def test_skips_transactions_with_directs(self, db_session):
        account, cat, txs = _seed_data(db_session)

        db_session.add(
            P4xCategoryDirect(
                p4x_transaction_id=txs[0].id,
                p4x_category_id=cat.id,
                amount=15.0,
            )
        )
        db_session.commit()

        f = P4xCategoryFilter(
            name="direct_excl_test",
            p4x_account_id=account.id,
            subject_mode="starts",
            subject="mitgliedsbeitrag",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(f)
        db_session.commit()

        apply_single_filter(db_session, f)
        assert get_filter_hit_count(db_session, f) == 1


class TestApplyAll:
    def test_apply_all_filters(self, db_session):
        account, cat, _txs = _seed_data(db_session)
        cat2 = P4xCategory(
            name="spende",
            label="Spende",
            background_color="#000",
            text_color="#fff",
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cat2)
        db_session.commit()
        db_session.refresh(cat2)

        f1 = P4xCategoryFilter(
            name="f1",
            p4x_account_id=account.id,
            subject_mode="starts",
            subject="mitgliedsbeitrag",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        f2 = P4xCategoryFilter(
            name="f2",
            p4x_account_id=account.id,
            subject_mode="equals",
            subject="Spende Verein",
            p4x_category_id=cat2.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add_all([f1, f2])
        db_session.commit()

        apply_all_category_filters(db_session)

        assert get_filter_hit_count(db_session, f1) == 2
        assert get_filter_hit_count(db_session, f2) == 1

    def test_apply_all_with_truncate(self, db_session):
        account, cat, _txs = _seed_data(db_session)
        f = P4xCategoryFilter(
            name="trunc_test",
            p4x_account_id=account.id,
            subject_mode="starts",
            subject="mitgliedsbeitrag",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(f)
        db_session.commit()

        apply_all_category_filters(db_session)
        count_before = db_session.query(P4xCategoryFilterHit).count()
        assert count_before > 0

        apply_all_category_filters(db_session, truncate_first=True)
        count_after = db_session.query(P4xCategoryFilterHit).count()
        assert count_after == count_before
