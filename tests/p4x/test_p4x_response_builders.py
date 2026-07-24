from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.contact import Contact
from app.models.member import Member
from app.models.org import Org
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
from app.models.p4x_transaction import P4xTransaction
from app.models.state import State
from app.services import p4x_service
from app.services.p4x_response_builders import (
    build_account_response,
    build_category_response,
    build_filter_response,
    build_transaction_response,
    get_account_or_404,
    get_filter_or_404,
    get_transaction_for_account,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _seed(db) -> tuple[P4xAccount, Member, Contact]:
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            State(id="up", label="UP", order=1),
        ]
    )
    db.commit()

    member = Member(
        vorname="Michael",
        nachname="Schimpl",
        couleurname="Kopernikus",
        email="test@test.at",
        auth_password="x",
        auth_locked=False,
        org_id="vbw",
        state_id="up",
    )
    contact = Contact(kontakttyp="organisation", name="Netcup GmbH", org_id="vbw")
    account = P4xAccount(
        iban="AT942011100005301947",
        bic="GIBAATWWXXX",
        label="Girokonto",
        init_date=date(2017, 1, 1),
        init_balance=0,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add_all([member, contact, account])
    db.commit()
    db.refresh(member)
    db.refresh(contact)
    db.refresh(account)
    return account, member, contact


def _create_tx(db, account: P4xAccount, iban: str = "DE001") -> P4xTransaction:
    tx = P4xTransaction(
        sha256_hash=f"resp_builder_tx_{iban}",
        booking=date(2026, 3, 20),
        valuation=date(2026, 3, 20),
        iban=iban,
        amount=Decimal("15.00"),
        subject="test",
        p4x_account_id=account.id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


class TestBuildTransactionResponse:
    def test_bare_transaction_has_no_partner_or_directs(self, db_session):
        account, _member, _contact = _seed(db_session)
        tx = _create_tx(db_session, account)

        resp = build_transaction_response(tx, db_session)

        assert resp.id == tx.id
        assert resp.partner is None
        assert resp.delegating_partner is None
        assert resp.p4x_category_directs == []
        assert resp.p4x_category_filters == []
        assert resp.p4x_account_cn == account.cn
        assert resp.p4x_account_iban == account.iban

    def test_includes_active_partner(self, db_session):
        account, member, _contact = _seed(db_session)
        tx = _create_tx(db_session, account)
        p4x_service.set_transaction_partner(
            db_session, tx, {"type": "member", "id": member.id}, False, None
        )
        db_session.refresh(tx)

        resp = build_transaction_response(tx, db_session)

        assert resp.partner is not None
        assert resp.partner.type == "member"
        assert resp.partner.id == member.id
        assert resp.partner.cn == member.cn

    def test_soft_deleted_partner_is_excluded(self, db_session):
        account, member, _contact = _seed(db_session)
        tx = _create_tx(db_session, account)
        p4x_service.set_transaction_partner(
            db_session, tx, {"type": "member", "id": member.id}, False, None
        )
        db_session.refresh(tx)
        tx.partner.deleted_at = _now()
        db_session.commit()
        db_session.refresh(tx)

        resp = build_transaction_response(tx, db_session)

        assert resp.partner is None

    def test_includes_delegating_partner(self, db_session):
        account, member, contact = _seed(db_session)
        tx = _create_tx(db_session, account)
        p4x_service.set_transaction_partner(
            db_session,
            tx,
            {"type": "member", "id": member.id},
            True,
            {"type": "contact", "id": contact.id},
        )
        db_session.refresh(tx)

        resp = build_transaction_response(tx, db_session)

        assert resp.delegating_partner is not None
        assert resp.delegating_partner.type == "contact"
        assert resp.delegating_partner.id == contact.id
        assert resp.delegating_partner.cn == contact.cn

    def test_includes_active_category_directs_only(self, db_session):
        account, _member, _contact = _seed(db_session)
        tx = _create_tx(db_session, account)
        category = P4xCategory(
            name="spende",
            label="Spende",
            background_color="#ffffff",
            text_color="#000000",
            protected=False,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(category)
        db_session.commit()
        active = P4xCategoryDirect(
            p4x_transaction_id=tx.id,
            p4x_category_id=category.id,
            amount=Decimal("15.00"),
        )
        deleted = P4xCategoryDirect(
            p4x_transaction_id=tx.id,
            p4x_category_id=category.id,
            amount=Decimal("15.00"),
            deleted_at=_now(),
        )
        db_session.add_all([active, deleted])
        db_session.commit()

        resp = build_transaction_response(tx, db_session)

        assert len(resp.p4x_category_directs) == 1
        assert resp.p4x_category_directs[0].id == active.id

    def test_includes_category_filter_hits_with_hit_count(self, db_session):
        account, _member, _contact = _seed(db_session)
        tx = _create_tx(db_session, account)
        category = P4xCategory(
            name="beitrag",
            label="Beitrag",
            background_color="#ffffff",
            text_color="#000000",
            protected=False,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(category)
        db_session.commit()
        category_filter = P4xCategoryFilter(
            name="Alle Beiträge",
            p4x_account_id=account.id,
            subject_mode="contains",
            p4x_category_id=category.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(category_filter)
        db_session.commit()
        hit = P4xCategoryFilterHit(
            p4x_transaction_id=tx.id,
            p4x_category_filter_id=category_filter.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(hit)
        db_session.commit()

        resp = build_transaction_response(tx, db_session)

        assert len(resp.p4x_category_filters) == 1
        assert resp.p4x_category_filters[0].id == category_filter.id
        assert resp.p4x_category_filters[0].hitCount == 1
        assert resp.p4x_category_filters[0].p4x_account_label == account.label


class TestBuildAccountResponse:
    def test_account_with_no_transactions(self, db_session):
        account, _member, _contact = _seed(db_session)

        resp = build_account_response(db_session, account)

        assert resp.transactions_count == 0
        assert resp.transactions_latest is None
        assert resp.bic == account.bic

    def test_account_with_transactions_reports_count_and_latest(self, db_session):
        account, _member, _contact = _seed(db_session)
        _create_tx(db_session, account, iban="DE001")
        later = _create_tx(db_session, account, iban="DE002")
        later.booking = date(2026, 6, 1)
        db_session.commit()

        resp = build_account_response(db_session, account)

        assert resp.transactions_count == 2
        assert resp.transactions_latest == "2026-06-01"


class TestGetAccountOr404:
    def test_returns_active_account(self, db_session):
        account, _member, _contact = _seed(db_session)
        assert get_account_or_404(db_session, account.id).id == account.id

    def test_raises_404_for_unknown_id(self, db_session):
        with pytest.raises(HTTPException) as exc_info:
            get_account_or_404(db_session, 999999)
        assert exc_info.value.status_code == 404

    def test_raises_404_for_soft_deleted_account(self, db_session):
        account, _member, _contact = _seed(db_session)
        account.deleted_at = _now()
        db_session.commit()
        with pytest.raises(HTTPException) as exc_info:
            get_account_or_404(db_session, account.id)
        assert exc_info.value.status_code == 404


class TestGetTransactionForAccount:
    def test_returns_matching_transaction(self, db_session):
        account, _member, _contact = _seed(db_session)
        tx = _create_tx(db_session, account)
        result = get_transaction_for_account(db_session, account.id, tx.id)
        assert result.id == tx.id

    def test_raises_404_for_wrong_account(self, db_session):
        account, _member, _contact = _seed(db_session)
        other_account = P4xAccount(
            iban="AT000000000000000000",
            bic="GIBAATWWXXX",
            label="Zweitkonto",
            init_date=date(2017, 1, 1),
            init_balance=0,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(other_account)
        db_session.commit()
        tx = _create_tx(db_session, account)

        with pytest.raises(HTTPException) as exc_info:
            get_transaction_for_account(db_session, other_account.id, tx.id)
        assert exc_info.value.status_code == 404

    def test_raises_404_for_unknown_transaction(self, db_session):
        account, _member, _contact = _seed(db_session)
        with pytest.raises(HTTPException) as exc_info:
            get_transaction_for_account(db_session, account.id, 999999)
        assert exc_info.value.status_code == 404


class TestBuildCategoryResponse:
    def test_reports_usage(self, db_session):
        account, _member, _contact = _seed(db_session)
        category = P4xCategory(
            name="spende",
            label="Spende",
            background_color="#ffffff",
            text_color="#000000",
            protected=False,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(category)
        db_session.commit()
        category_filter = P4xCategoryFilter(
            name="Spenden-Filter",
            p4x_account_id=account.id,
            subject_mode="contains",
            p4x_category_id=category.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(category_filter)
        db_session.commit()

        resp = build_category_response(db_session, category)

        assert resp.id == category.id
        assert resp.used["filter"] == 1
        assert resp.used["direct"] == 0


class TestBuildFilterResponse:
    def test_reports_hit_count_and_account_label(self, db_session):
        account, _member, _contact = _seed(db_session)
        category = P4xCategory(
            name="beitrag",
            label="Beitrag",
            background_color="#ffffff",
            text_color="#000000",
            protected=False,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(category)
        db_session.commit()
        category_filter = P4xCategoryFilter(
            name="Alle Beiträge",
            p4x_account_id=account.id,
            subject_mode="contains",
            p4x_category_id=category.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(category_filter)
        db_session.commit()

        resp = build_filter_response(db_session, category_filter)

        assert resp.id == category_filter.id
        assert resp.p4x_account_label == account.label
        assert resp.hitCount == 0


class TestGetFilterOr404:
    def test_returns_existing_filter(self, db_session):
        account, _member, _contact = _seed(db_session)
        category = P4xCategory(
            name="spende",
            label="Spende",
            background_color="#ffffff",
            text_color="#000000",
            protected=False,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(category)
        db_session.commit()
        category_filter = P4xCategoryFilter(
            name="Ein Filter",
            p4x_account_id=account.id,
            subject_mode="contains",
            p4x_category_id=category.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(category_filter)
        db_session.commit()

        found = get_filter_or_404(db_session, category_filter.id)
        assert found.id == category_filter.id

    def test_raises_404_for_unknown_filter(self, db_session):
        with pytest.raises(HTTPException) as exc_info:
            get_filter_or_404(db_session, 999999)
        assert exc_info.value.status_code == 404
