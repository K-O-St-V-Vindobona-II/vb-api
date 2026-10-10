"""Rules the database enforces for p4x accounts, category filters and fees:
one active account per IBAN (blanks ignored), a filter needs a criterion and a
fee starts on the first day of a month."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.enums import SubjectMode
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_fee import P4xFee
from app.schemas.p4x import AccountSaveRequest
from app.services import p4x_account_service
from tests.p4x.test_p4x_service_queries import _create_account, _now

IBAN = "AT942011100005301947"
IBAN_WITH_BLANKS = "AT94 2011 1000 0530 1947"
OTHER_IBAN = "AT611904300234573201"


def _request(iban: str) -> AccountSaveRequest:
    return AccountSaveRequest(
        iban=iban,
        bic="GIBAATWWXXX",
        label="Konto",
        init_date=date(2020, 1, 1),
        init_balance=Decimal(0),
    )


def _account(iban: str, *, deleted: bool = False) -> P4xAccount:
    return P4xAccount(
        iban=iban,
        label="a",
        init_balance=0,
        deleted_at=datetime.now(UTC) if deleted else None,
    )


class TestAccountIban:
    def test_iban_of_a_deleted_account_can_be_added_again(self, db_session):
        old = _create_account(db_session)
        old.deleted_at = datetime.now(UTC)
        db_session.commit()

        account = p4x_account_service.create_account(db_session, _request(IBAN))

        assert account.iban == IBAN

    def test_the_same_iban_with_blanks_is_a_conflict(self, db_session):
        _create_account(db_session)

        with pytest.raises(HTTPException) as exc:
            p4x_account_service.create_account(db_session, _request(IBAN_WITH_BLANKS))

        assert exc.value.status_code == 409

    def test_an_iban_without_blanks_conflicts_with_a_stored_spelling_with_blanks(
        self, db_session
    ):
        db_session.add(_account(IBAN_WITH_BLANKS))
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            p4x_account_service.create_account(db_session, _request(IBAN))

        assert exc.value.status_code == 409

    def test_the_spelling_is_stored_as_typed(self, db_session):
        account = p4x_account_service.create_account(
            db_session, _request(IBAN_WITH_BLANKS)
        )

        assert account.iban == IBAN_WITH_BLANKS

    def test_updating_an_account_to_the_spelling_of_another_is_a_conflict(
        self, db_session
    ):
        _create_account(db_session)
        other = p4x_account_service.create_account(db_session, _request(OTHER_IBAN))

        with pytest.raises(HTTPException) as exc:
            p4x_account_service.update_account(
                db_session, other, _request(IBAN_WITH_BLANKS)
            )

        assert exc.value.status_code == 409

    def test_an_account_can_change_to_another_spelling_of_its_own_iban(
        self, db_session
    ):
        account = _create_account(db_session)

        updated = p4x_account_service.update_account(
            db_session, account, _request(IBAN_WITH_BLANKS)
        )

        assert updated.iban == IBAN_WITH_BLANKS

    def test_the_database_refuses_two_spellings_of_one_iban(self, db_session):
        db_session.add(_account(IBAN))
        db_session.commit()

        db_session.add(_account(IBAN_WITH_BLANKS))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_the_database_refuses_the_same_iban_twice(self, db_session):
        db_session.add(_account(IBAN))
        db_session.commit()

        db_session.add(_account(IBAN))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_deleted_accounts_may_share_an_iban_with_each_other_and_an_active_one(
        self, db_session
    ):
        db_session.add_all(
            [
                _account(IBAN, deleted=True),
                _account(IBAN_WITH_BLANKS, deleted=True),
                _account(IBAN),
            ]
        )

        db_session.commit()


class TestFilterCriterion:
    def _filter(self, db_session, **criteria):
        account = _create_account(db_session)
        category = P4xCategory(
            name="c",
            label="c",
            background_color="#000000",
            text_color="#ffffff",
            created_at=_now(),
            updated_at=_now(),
        )
        db_session.add(category)
        db_session.commit()
        return P4xCategoryFilter(
            name="f",
            p4x_account_id=account.id,
            p4x_category_id=category.id,
            subject_mode=SubjectMode.CONTAINS,
            created_at=_now(),
            updated_at=_now(),
            **criteria,
        )

    @pytest.mark.parametrize(
        "criteria",
        [
            {},
            {"iban": ""},
            {"subject": ""},
            {"iban": "", "subject": ""},
            {"iban": None, "subject": None},
        ],
    )
    def test_a_filter_without_any_criterion_is_refused(self, db_session, criteria):
        db_session.add(self._filter(db_session, **criteria))

        with pytest.raises(IntegrityError):
            db_session.commit()

    @pytest.mark.parametrize(
        "criteria",
        [
            {"iban": IBAN},
            {"min_amount": Decimal(1)},
            {"max_amount": Decimal(9)},
            {"min_amount": Decimal(0)},
            {"subject": "Beitrag"},
        ],
    )
    def test_one_criterion_is_enough(self, db_session, criteria):
        db_session.add(self._filter(db_session, **criteria))

        db_session.commit()


class TestFeeStart:
    @pytest.mark.parametrize(
        "start", [date(2026, 1, 15), date(2026, 1, 31), date(2026, 2, 2)]
    )
    def test_a_fee_starting_on_another_day_is_refused(self, db_session, start):
        db_session.add(P4xFee(start=start, fee=Decimal(20), protected=False))

        with pytest.raises(IntegrityError):
            db_session.commit()

    @pytest.mark.parametrize(
        "start", [date(2026, 1, 1), date(2026, 2, 1), date(2026, 12, 1)]
    )
    def test_a_fee_starting_on_the_first_is_accepted(self, db_session, start):
        db_session.add(P4xFee(start=start, fee=Decimal(20), protected=False))

        db_session.commit()
