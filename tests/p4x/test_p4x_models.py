from datetime import UTC, date, datetime

from app.models.contact import Contact
from app.models.member import Member
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
from app.models.p4x_fee import P4xFee
from app.models.p4x_partner import P4xPartner
from app.models.p4x_specialcontact import P4xSpecialcontact
from app.models.p4x_summary_order import P4xSummaryOrder
from app.models.p4x_transaction import P4xTransaction


def _now() -> datetime:
    return datetime.now(UTC)


def _seed_account(db) -> P4xAccount:
    account = P4xAccount(
        iban="AT94 2011 1000 0530 1947",
        bic="GIBAATWWXXX",
        label="Girokonto",
        init_date=date(2017, 1, 1),
        init_balance=0.0,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _seed_category(db) -> P4xCategory:
    category = P4xCategory(
        name="eingang.mitgliedsbeitrag",
        label="Mitgliedsbeitrag",
        background_color="#336600",
        text_color="#ffffff",
        protected=False,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


class TestP4xAccount:
    def test_create_and_read(self, db_session):
        account = _seed_account(db_session)
        assert account.id is not None
        assert account.iban == "AT94 2011 1000 0530 1947"
        assert account.bic == "GIBAATWWXXX"
        assert account.init_balance == 0.0

    def test_cn_property(self, db_session):
        account = _seed_account(db_session)
        assert account.cn == "Girokonto"

    def test_cn_strips_whitespace(self, db_session):
        account = P4xAccount(
            iban="AT00TEST",
            bic="TEST",
            label="  Testkonto  ",
            init_date=date(2020, 1, 1),
            init_balance=0.0,
        )
        db_session.add(account)
        db_session.commit()
        assert account.cn == "Testkonto"

    def test_soft_delete_field(self, db_session):
        account = _seed_account(db_session)
        assert account.deleted_at is None
        account.deleted_at = _now()
        db_session.commit()
        assert account.deleted_at is not None


class TestP4xTransaction:
    def test_create_with_account(self, db_session):
        account = _seed_account(db_session)
        tx = P4xTransaction(
            sha256_hash="abc123def456",
            booking=date(2026, 3, 20),
            valuation=date(2026, 3, 20),
            iban="DE49100110012624770917",
            amount=15.00,
            subject="monatlicher MB v. Kopernikus",
            p4x_account_id=account.id,
            raw='{"test": true}',
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(tx)
        db_session.commit()
        db_session.refresh(tx)
        assert tx.id is not None
        assert tx.account.id == account.id
        assert tx.has_attachment is False

    def test_has_attachment_property(self, db_session):
        account = _seed_account(db_session)
        tx = P4xTransaction(
            sha256_hash="hash_with_attachment",
            booking=date(2026, 1, 1),
            valuation=date(2026, 1, 1),
            iban="AT00TEST",
            amount=-100.0,
            subject="test",
            p4x_account_id=account.id,
            attachment="dGVzdA==",
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(tx)
        db_session.commit()
        assert tx.has_attachment is True

    def test_account_relationship(self, db_session):
        account = _seed_account(db_session)
        tx = P4xTransaction(
            sha256_hash="rel_test",
            booking=date(2026, 1, 1),
            valuation=date(2026, 1, 1),
            iban="AT00TEST",
            amount=50.0,
            subject="test",
            p4x_account_id=account.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(tx)
        db_session.commit()
        db_session.refresh(account)
        assert len(account.transactions) == 1
        assert account.transactions[0].sha256_hash == "rel_test"


class TestP4xCategory:
    def test_create(self, db_session):
        cat = _seed_category(db_session)
        assert cat.id is not None
        assert cat.name == "eingang.mitgliedsbeitrag"
        assert cat.protected is False

    def test_unique_name(self, db_session):
        _seed_category(db_session)
        dup = P4xCategory(
            name="eingang.mitgliedsbeitrag",
            label="Dup",
            background_color="#000",
            text_color="#fff",
        )
        db_session.add(dup)
        import pytest
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            db_session.commit()


class TestP4xCategoryFilter:
    def test_create_with_relationships(self, db_session):
        account = _seed_account(db_session)
        category = _seed_category(db_session)
        f = P4xCategoryFilter(
            name="MB:starts.mitgliedsbeitrag",
            p4x_account_id=account.id,
            iban=None,
            min_amount=0.0,
            max_amount=30.0,
            subject_mode="starts",
            subject="mitgliedsb.",
            p4x_category_id=category.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)
        assert f.id is not None
        assert f.account.id == account.id
        assert f.category.id == category.id


class TestP4xCategoryDirect:
    def test_create(self, db_session):
        account = _seed_account(db_session)
        category = _seed_category(db_session)
        tx = P4xTransaction(
            sha256_hash="direct_test",
            booking=date(2026, 1, 1),
            valuation=date(2026, 1, 1),
            iban="AT00TEST",
            amount=30.0,
            subject="test",
            p4x_account_id=account.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(tx)
        db_session.commit()
        direct = P4xCategoryDirect(
            p4x_transaction_id=tx.id,
            p4x_category_id=category.id,
            amount=30.0,
        )
        db_session.add(direct)
        db_session.commit()
        db_session.refresh(tx)
        assert len(tx.category_directs) == 1
        assert tx.category_directs[0].amount == 30.0
        assert tx.category_directs[0].category.name == "eingang.mitgliedsbeitrag"


class TestP4xCategoryFilterHit:
    def test_unique_constraint(self, db_session):
        account = _seed_account(db_session)
        category = _seed_category(db_session)
        tx = P4xTransaction(
            sha256_hash="hit_test",
            booking=date(2026, 1, 1),
            valuation=date(2026, 1, 1),
            iban="AT00TEST",
            amount=15.0,
            subject="test",
            p4x_account_id=account.id,
            created_at=_now(),
            updated_at=_now(),
        )
        f = P4xCategoryFilter(
            name="test_filter",
            p4x_account_id=account.id,
            subject_mode="equals",
            p4x_category_id=category.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add_all([tx, f])
        db_session.commit()
        hit = P4xCategoryFilterHit(
            p4x_transaction_id=tx.id,
            p4x_category_filter_id=f.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(hit)
        db_session.commit()
        assert hit.id is not None
        assert hit.transaction.id == tx.id
        assert hit.category_filter.id == f.id


class TestP4xPartner:
    def test_create(self, db_session):
        member = Member(vorname="Test", nachname="Partner")
        db_session.add(member)
        db_session.commit()

        partner = P4xPartner(
            iban="AT761200023423416700",
            member_id=member.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(partner)
        db_session.commit()
        assert partner.id is not None
        assert partner.partner_type == "member"

    def test_soft_delete(self, db_session):
        contact = Contact(kontakttyp="person", name="Soft Delete Contact")
        db_session.add(contact)
        db_session.commit()

        partner = P4xPartner(
            iban="AT00SOFTDELETE",
            contact_id=contact.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(partner)
        db_session.commit()
        assert partner.deleted_at is None
        partner.deleted_at = _now()
        db_session.commit()
        assert partner.deleted_at is not None


class TestP4xFee:
    def test_create(self, db_session):
        fee = P4xFee(start=date(2017, 1, 1), fee=10.0, protected=True)
        db_session.add(fee)
        db_session.commit()
        loaded = db_session.query(P4xFee).first()
        assert loaded.fee == 10.0
        assert loaded.protected is True

    def test_multiple_fees(self, db_session):
        db_session.add_all(
            [
                P4xFee(start=date(2017, 1, 1), fee=10.0, protected=True),
                P4xFee(start=date(2024, 6, 1), fee=15.0, protected=False),
            ]
        )
        db_session.commit()
        fees = db_session.query(P4xFee).order_by(P4xFee.start).all()
        assert len(fees) == 2
        assert fees[0].fee == 10.0
        assert fees[1].fee == 15.0


class TestP4xSpecialcontact:
    def test_create(self, db_session):
        sc = P4xSpecialcontact(cn="Konto-Intern(Sparkassen-Information)")
        db_session.add(sc)
        db_session.commit()
        assert sc.id is not None
        assert sc.search_label == "Spezial: Konto-Intern(Sparkassen-Information)"


class TestP4xSummaryOrder:
    def test_create(self, db_session):
        member = Member(vorname="Test", nachname="Orderer")
        db_session.add(member)
        db_session.commit()

        order = P4xSummaryOrder(
            ordered_by=member.id,
            email="test@example.com",
            summary_start=date(2025, 1, 1),
            summary_end=date(2025, 12, 1),
            finished_ok=False,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(order)
        db_session.commit()
        assert order.id is not None
        assert order.finished_ok is False


class TestPartnerTransactionRelationship:
    def test_transaction_finds_partner_by_iban(self, db_session):
        account = _seed_account(db_session)
        member = Member(vorname="Test", nachname="Relationship")
        db_session.add(member)
        db_session.commit()

        partner = P4xPartner(
            iban="DE49100110012624770917",
            member_id=member.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(partner)
        db_session.commit()
        tx = P4xTransaction(
            sha256_hash="partner_rel_test",
            booking=date(2026, 3, 20),
            valuation=date(2026, 3, 20),
            iban="DE49100110012624770917",
            amount=15.0,
            subject="test",
            p4x_account_id=account.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(tx)
        db_session.commit()
        db_session.refresh(tx)
        assert tx.partner is not None
        assert tx.partner.partner_type == "member"
        assert tx.partner.partner_id == member.id
