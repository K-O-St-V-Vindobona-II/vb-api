from datetime import UTC, date, datetime

from app.models.member import Member
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
from app.models.p4x_partner import P4xPartner
from app.models.p4x_transaction import P4xTransaction
from app.services.p4x_service import (
    apply_single_filter,
    delete_category_filter,
    filter_to_direct,
    get_filter_hit_count,
    get_filter_hits,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _seed(db) -> tuple[P4xAccount, P4xCategory, list[P4xTransaction]]:
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
        name="test.kategorie",
        label="Test Kat",
        background_color="#336600",
        text_color="#ffffff",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add_all([account, cat])
    db.commit()
    db.refresh(account)
    db.refresh(cat)

    txs: list[P4xTransaction] = []
    for i, (subject, amount, iban) in enumerate(
        [
            ("MB Kopernikus", 15.0, "DE001"),
            ("MB Roland", 30.0, "AT001"),
            ("Spende Verein", 100.0, "AT003"),
        ]
    ):
        tx = P4xTransaction(
            sha256hash=f"fext_{i}",
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


class TestDeleteCategoryFilter:
    def test_deletes_filter_and_hits(self, db_session):
        account, cat, _txs = _seed(db_session)

        cf = P4xCategoryFilter(
            name="del_test",
            p4x_account_id=account.id,
            subject_mode="starts",
            subject="MB",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cf)
        db_session.commit()
        db_session.refresh(cf)

        apply_single_filter(db_session, cf)
        assert get_filter_hit_count(db_session, cf) == 2

        filter_id = cf.id
        delete_category_filter(db_session, cf)

        remaining_hits = (
            db_session.query(P4xCategoryFilterHit)
            .filter(P4xCategoryFilterHit.p4x_category_filter_id == filter_id)
            .count()
        )
        assert remaining_hits == 0

        remaining_filter = (
            db_session.query(P4xCategoryFilter)
            .filter(P4xCategoryFilter.id == filter_id)
            .first()
        )
        assert remaining_filter is None


class TestFilterToDirect:
    def test_converts_filter_hits_to_directs(self, db_session):
        account, cat, txs = _seed(db_session)

        member = Member(vorname="Test", nachname="Filter")
        db_session.add(member)
        db_session.commit()

        # Assign all transactions a partner to eliminate partner warnings
        for tx in txs:
            existing = (
                db_session.query(P4xPartner).filter(P4xPartner.iban == tx.iban).first()
            )
            if not existing:
                p = P4xPartner(
                    iban=tx.iban,
                    member_id=member.id,
                    created_at=_now(),
                    updated_at=_now(),
                )
                db_session.add(p)
        db_session.commit()

        cf = P4xCategoryFilter(
            name="conv_test",
            p4x_account_id=account.id,
            subject_mode="equals",
            subject="Spende Verein",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cf)
        db_session.commit()
        db_session.refresh(cf)

        apply_single_filter(db_session, cf)
        assert get_filter_hit_count(db_session, cf) == 1

        # Create a second filter giving the remaining txs exactly 1 hit each
        cf2 = P4xCategoryFilter(
            name="catchall",
            p4x_account_id=account.id,
            subject_mode="starts",
            subject="MB",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cf2)
        db_session.commit()
        db_session.refresh(cf2)
        apply_single_filter(db_session, cf2)

        result = filter_to_direct(db_session, cf)
        assert result is None

        directs = (
            db_session.query(P4xCategoryDirect)
            .filter(
                P4xCategoryDirect.p4x_category_id == cat.id,
                P4xCategoryDirect.deleted_at.is_(None),
            )
            .all()
        )
        assert len(directs) >= 1

    def test_blocks_when_warnings_exist(self, db_session):
        account, cat, _txs = _seed(db_session)

        cf = P4xCategoryFilter(
            name="block_test",
            p4x_account_id=account.id,
            subject_mode="equals",
            subject="Spende Verein",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cf)
        db_session.commit()
        db_session.refresh(cf)

        apply_single_filter(db_session, cf)

        # Warnings exist (partner warnings because no partners assigned)
        result = filter_to_direct(db_session, cf)
        assert result is not None
        assert "Warnungen" in result


class TestFilterEngineEmptyIban:
    def test_empty_iban_filter_does_not_filter_by_iban(self, db_session):
        """A filter with iban='' should NOT restrict by IBAN."""
        account, cat, _txs = _seed(db_session)

        cf = P4xCategoryFilter(
            name="empty_iban_test",
            p4x_account_id=account.id,
            iban="",
            subject_mode="starts",
            subject="MB",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cf)
        db_session.commit()
        db_session.refresh(cf)

        apply_single_filter(db_session, cf)
        # Should match all 'MB' transactions regardless of IBAN
        assert get_filter_hit_count(db_session, cf) == 2


class TestGetFilterHitsExtended:
    def test_returns_empty_when_all_hits_have_directs(self, db_session):
        account, cat, txs = _seed(db_session)

        cf = P4xCategoryFilter(
            name="alldir_test",
            p4x_account_id=account.id,
            subject_mode="equals",
            subject="Spende Verein",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cf)
        db_session.commit()
        db_session.refresh(cf)

        apply_single_filter(db_session, cf)
        assert get_filter_hit_count(db_session, cf) == 1

        # Add a direct assignment for the "Spende Verein" transaction
        spende_tx = next(t for t in txs if t.subject == "Spende Verein")
        db_session.add(
            P4xCategoryDirect(
                p4x_transaction_id=spende_tx.id,
                p4x_category_id=cat.id,
                amount=100.0,
            )
        )
        db_session.commit()

        hits = get_filter_hits(db_session, cf)
        assert hits == []

    def test_returns_only_non_direct_hits(self, db_session):
        account, cat, txs = _seed(db_session)

        cf = P4xCategoryFilter(
            name="nondirect_test",
            p4x_account_id=account.id,
            subject_mode="starts",
            subject="MB",
            p4x_category_id=cat.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(cf)
        db_session.commit()
        db_session.refresh(cf)

        apply_single_filter(db_session, cf)
        # 2 hits: MB Kopernikus and MB Roland

        # Assign direct to one of them
        mb_tx = next(t for t in txs if t.subject == "MB Kopernikus")
        db_session.add(
            P4xCategoryDirect(
                p4x_transaction_id=mb_tx.id,
                p4x_category_id=cat.id,
                amount=15.0,
            )
        )
        db_session.commit()

        hits = get_filter_hits(db_session, cf)
        assert len(hits) == 1
        assert hits[0].subject == "MB Roland"
