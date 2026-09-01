"""Regression tests for Slice 5 of the backend remediation plan: several
p4x service flows used to commit multiple times mid-flow instead of once
at the end. Each test forces a failure in the second half of the flow and
verifies that a subsequent rollback() discards ALL of it - proving the
flow is now a single atomic transaction instead of partially persisting
work before the failure.
"""

import json
from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest

from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
from app.models.p4x_partner import P4xPartner
from app.models.p4x_transaction import P4xTransaction
from app.schemas.p4x import CategoryFilterSaveRequest
from app.services import p4x_category_service, p4x_import_service
from app.services.p4x_import_service import parse_george_json


def _now() -> datetime:
    return datetime.now(UTC)


def _create_account(db, iban: str = "AT94 2011 1000 0530 1947") -> P4xAccount:
    account = P4xAccount(
        iban=iban,
        bic="GIBAATWWXXX",
        label="Girokonto",
        init_date=date(2015, 1, 1),
        init_balance=0.0,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _make_george_entry(iban: str = "DE49100110012624770917") -> dict:
    return {
        "booking": "2026-03-20T00:00:00.000+0100",
        "valuation": "2026-03-20T00:00:00.000+0100",
        "partnerAccount": {"iban": iban},
        "amount": {"value": 1500, "precision": 2},
        "reference": "monatlicher MB",
        "receiverReference": "",
    }


class TestImportAndApplyFiltersAtomicity:
    def test_rolls_back_import_if_filter_application_fails(self, db_session):
        account = _create_account(db_session)
        entry = _make_george_entry()
        raw_json = json.dumps([entry])
        parsed = parse_george_json("GIBAATWWXXX", raw_json)
        assert parsed.success

        with (
            patch(
                "app.services.p4x_category_service.apply_all_category_filters_core",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError),
        ):
            p4x_import_service.import_and_apply_filters(
                db_session, account, parsed.entries, [entry]
            )

        db_session.rollback()
        assert db_session.query(P4xTransaction).count() == 0


class TestFilterToDirectAtomicity:
    def _seed_two_hits(self, db):
        account = _create_account(db)
        category = P4xCategory(
            name="test.cat",
            label="Test",
            background_color="#000000",
            text_color="#ffffff",
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(category)
        db.commit()
        db.refresh(category)

        category_filter = P4xCategoryFilter(
            name="test.filter",
            p4x_account_id=account.id_uuid,
            subject="MB",
            subject_mode="contains",
            p4x_category_id=category.id_uuid,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(category_filter)
        # A matching P4xPartner keeps get_warnings_partner() from flagging
        # these transactions - filter_to_direct() refuses to run at all
        # while any warnings are open.
        db.add(
            P4xPartner(
                iban="AT001",
                p4x_account_id=account.id_uuid,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        db.commit()
        db.refresh(category_filter)

        tx_ids = []
        for i in range(2):
            tx = P4xTransaction(
                sha256_hash=f"atomicity_tx_{i}",
                booking=date(2026, 3, 15),
                valuation=date(2026, 3, 15),
                iban="AT001",
                amount=100.0,
                subject="MB",
                p4x_account_id=account.id_uuid,
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(tx)
            db.commit()
            db.refresh(tx)
            tx_ids.append(tx.id)
            db.add(
                P4xCategoryFilterHit(
                    p4x_transaction_id=tx.id,
                    p4x_category_filter_id=category_filter.id,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
        db.commit()
        return category_filter, tx_ids

    def test_rolls_back_all_conversions_if_one_fails(self, db_session):
        category_filter, _tx_ids = self._seed_two_hits(db_session)

        with (
            patch(
                "app.services.p4x_category_service._set_category_direct_core",
                side_effect=[None, RuntimeError("boom")],
            ),
            pytest.raises(RuntimeError),
        ):
            p4x_category_service.filter_to_direct(db_session, category_filter)

        db_session.rollback()
        assert db_session.query(P4xCategoryDirect).count() == 0
        assert (
            db_session.query(P4xCategoryFilterHit)
            .filter_by(p4x_category_filter_id=category_filter.id)
            .count()
            == 2
        )


class TestCategoryFilterCrudAtomicity:
    def _seed_account_and_category(self, db):
        account = _create_account(db)
        category = P4xCategory(
            name="crud.cat",
            label="Test",
            background_color="#000000",
            text_color="#ffffff",
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(category)
        db.commit()
        db.refresh(account)
        db.refresh(category)
        return account, category

    def test_create_rolls_back_if_filter_application_fails(self, db_session):
        account, category = self._seed_account_and_category(db_session)
        data = CategoryFilterSaveRequest(
            name="new.filter",
            p4x_account_id=account.id_uuid,
            iban=None,
            min_amount=None,
            max_amount=None,
            subject="MB",
            subject_mode="contains",
            p4x_category_id=category.id_uuid,
        )

        with (
            patch(
                "app.services.p4x_category_service.apply_all_category_filters_core",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError),
        ):
            p4x_category_service.create_category_filter(db_session, data)

        db_session.rollback()
        assert db_session.query(P4xCategoryFilter).count() == 0

    def test_update_rolls_back_if_filter_application_fails(self, db_session):
        account, category = self._seed_account_and_category(db_session)
        category_filter = P4xCategoryFilter(
            name="existing.filter",
            p4x_account_id=account.id_uuid,
            subject="OLD",
            subject_mode="contains",
            p4x_category_id=category.id_uuid,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(category_filter)
        db_session.commit()
        db_session.refresh(category_filter)

        data = CategoryFilterSaveRequest(
            name="existing.filter",
            p4x_account_id=account.id_uuid,
            iban=None,
            min_amount=None,
            max_amount=None,
            subject="NEW",
            subject_mode="contains",
            p4x_category_id=category.id_uuid,
        )

        with (
            patch(
                "app.services.p4x_category_service.apply_all_category_filters_core",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError),
        ):
            p4x_category_service.update_category_filter(
                db_session, category_filter, data
            )

        db_session.rollback()
        db_session.refresh(category_filter)
        assert category_filter.subject == "OLD"


class TestUnsetCategoryDirectAtomicity:
    def test_rolls_back_if_filter_application_fails(self, db_session):
        account = _create_account(db_session)
        category = P4xCategory(
            name="unset.cat",
            label="Test",
            background_color="#000000",
            text_color="#ffffff",
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(category)
        db_session.commit()
        db_session.refresh(category)

        tx = P4xTransaction(
            sha256_hash="unset_atomicity_tx",
            booking=date(2026, 3, 15),
            valuation=date(2026, 3, 15),
            iban="AT001",
            amount=100.0,
            subject="Test",
            p4x_account_id=account.id_uuid,
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(tx)
        db_session.commit()
        db_session.refresh(tx)

        direct = P4xCategoryDirect(
            p4x_transaction_id=tx.id,
            p4x_category_id=category.id,
            amount=100.0,
        )
        db_session.add(direct)
        db_session.commit()

        with (
            patch(
                "app.services.p4x_category_service.apply_all_category_filters_core",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError),
        ):
            p4x_category_service.unset_category_direct(db_session, tx)

        db_session.rollback()
        still_active = (
            db_session.query(P4xCategoryDirect)
            .filter(
                P4xCategoryDirect.p4x_transaction_id == tx.id,
                P4xCategoryDirect.deleted_at.is_(None),
            )
            .count()
        )
        assert still_active == 1
