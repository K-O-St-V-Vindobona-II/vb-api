"""The stored filter hits of a transaction follow every write path: a hit exists
exactly when the transaction is active, has no direct assignment and matches the
filter. Readers leave out transactions with a direct assignment, so only the
hits of the others are compared."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models.member import Member
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_direct import P4xCategoryDirect
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
from app.models.p4x_partner import P4xPartner
from app.models.p4x_transaction import P4xTransaction
from app.schemas.p4x import CategoryFilterSaveRequest
from app.services import p4x_import_service
from app.services.p4x_category_service import (
    _apply_category_filter_criteria,
    apply_all_category_filters,
    create_category_filter,
    delete_category_filter,
    filter_to_direct,
    set_category_direct,
    unset_category_direct,
    update_category_filter,
)
from app.services.p4x_import_service import GEORGE_BIC, parse_george_json


def _now() -> datetime:
    return datetime.now(UTC)


def _direct_ids(db) -> set:
    return {
        row[0]
        for row in db.query(P4xCategoryDirect.p4x_transaction_id).filter(
            P4xCategoryDirect.deleted_at.is_(None)
        )
    }


def expected_hits(db) -> set[tuple]:
    """What the filters select right now, recomputed from the transactions."""
    with_directs = _direct_ids(db)
    pairs = set()
    for category_filter in db.query(P4xCategoryFilter):
        query = db.query(P4xTransaction.id).filter(P4xTransaction.deleted_at.is_(None))
        query = _apply_category_filter_criteria(query, category_filter, with_directs)
        pairs |= {(category_filter.id, tx_id) for (tx_id,) in query}
    return pairs


def stored_hits(db, *, masked: bool = False) -> set[tuple]:
    """The stored hits. Masked, the hits of transactions with a direct
    assignment are left out, as readers do."""
    with_directs = _direct_ids(db) if masked else set()
    return {
        (hit.p4x_category_filter_id, hit.p4x_transaction_id)
        for hit in db.query(P4xCategoryFilterHit)
        if hit.p4x_transaction_id not in with_directs
    }


def assert_hits_follow(db, *, masked: bool = False) -> None:
    db.expire_all()
    assert stored_hits(db, masked=masked) == expected_hits(db)


def _transaction(db, account, subject: str, amount: str, suffix: str):
    tx = P4xTransaction(
        sha256_hash=f"hits_{suffix}",
        booking=date(2026, 3, 15),
        valuation=date(2026, 3, 15),
        iban="AT001",
        amount=Decimal(amount),
        subject=subject,
        p4x_account_id=account.id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(tx)
    db.flush()
    return tx


def _request(account, category, subject: str = "Beitrag") -> CategoryFilterSaveRequest:
    return CategoryFilterSaveRequest(
        name="hits.filter",
        p4x_account_id=account.id,
        subject=subject,
        subject_mode="contains",
        p4x_category_id=category.id,
    )


@pytest.fixture
def setup(db_session):
    account = P4xAccount(
        iban="AT00HITS",
        bic="GIBAATWWXXX",
        label="Hits",
        init_date=date(2020, 1, 1),
        init_balance=0,
        created_at=_now(),
        updated_at=_now(),
    )
    category = P4xCategory(
        name="hits.cat",
        label="Hits",
        background_color="#000",
        text_color="#fff",
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add_all([account, category])
    db_session.flush()
    matching = _transaction(db_session, account, "Mitgliedsbeitrag", "100.00", "a")
    other = _transaction(db_session, account, "Spende", "50.00", "b")
    db_session.commit()
    return account, category, matching, other


class TestHitsFollowTheWritePaths:
    def test_creating_a_filter(self, db_session, setup):
        account, category, matching, _other = setup

        category_filter = create_category_filter(
            db_session, _request(account, category)
        )

        assert_hits_follow(db_session)
        assert expected_hits(db_session) == {(category_filter.id, matching.id)}

    def test_a_deleted_transaction_gets_no_hit(self, db_session, setup):
        account, category, matching, _other = setup
        gone = _transaction(db_session, account, "Beitrag alt", "10.00", "gone")
        gone.deleted_at = _now()
        db_session.commit()

        category_filter = create_category_filter(
            db_session, _request(account, category)
        )

        assert_hits_follow(db_session)
        assert expected_hits(db_session) == {(category_filter.id, matching.id)}

    def test_a_transaction_with_a_direct_assignment_gets_no_hit(
        self, db_session, setup
    ):
        account, category, matching, other = setup
        set_category_direct(
            db_session,
            matching,
            [{"p4x_category_id": category.id, "amount": matching.amount}],
        )

        category_filter = create_category_filter(
            db_session, _request(account, category)
        )
        update_category_filter(
            db_session, category_filter, _request(account, category, "Spende")
        )

        assert_hits_follow(db_session)
        assert stored_hits(db_session) == {(category_filter.id, other.id)}

    def test_changing_a_filter(self, db_session, setup):
        account, category, matching, other = setup
        category_filter = create_category_filter(
            db_session, _request(account, category)
        )

        update_category_filter(
            db_session, category_filter, _request(account, category, "Spende")
        )

        assert_hits_follow(db_session)
        assert expected_hits(db_session) == {(category_filter.id, other.id)}
        assert matching.id not in {tx for _f, tx in stored_hits(db_session)}

    def test_deleting_a_filter(self, db_session, setup):
        account, category, _matching, _other = setup
        category_filter = create_category_filter(
            db_session, _request(account, category)
        )

        delete_category_filter(db_session, category_filter)

        assert_hits_follow(db_session)
        assert stored_hits(db_session) == set()

    def test_assigning_a_category_directly(self, db_session, setup):
        account, category, matching, _other = setup
        create_category_filter(db_session, _request(account, category))

        set_category_direct(
            db_session,
            matching,
            [{"p4x_category_id": category.id, "amount": matching.amount}],
        )

        assert_hits_follow(db_session, masked=True)
        assert expected_hits(db_session) == set()

    def test_removing_the_direct_assignment_restores_the_hit(self, db_session, setup):
        account, category, matching, _other = setup
        set_category_direct(
            db_session,
            matching,
            [{"p4x_category_id": category.id, "amount": matching.amount}],
        )
        category_filter = create_category_filter(
            db_session, _request(account, category)
        )
        assert stored_hits(db_session) == set()

        unset_category_direct(db_session, matching)

        assert_hits_follow(db_session)
        assert stored_hits(db_session) == {(category_filter.id, matching.id)}

    def test_converting_a_filter_to_direct_assignments(self, db_session, setup):
        account, category, matching, _other = setup
        category_filter = create_category_filter(
            db_session, _request(account, category)
        )
        # every transaction needs exactly one filter hit before a conversion
        catch_all = _request(account, category, "Spende")
        catch_all.name = "hits.other"
        create_category_filter(db_session, catch_all)
        _partner_for_all(db_session, account)

        error = filter_to_direct(db_session, category_filter)

        assert error is None
        assert_hits_follow(db_session)
        assert matching.id in _direct_ids(db_session)

    def test_importing_an_entry_that_matches(self, db_session, setup):
        account, category, _matching, _other = setup
        category_filter = create_category_filter(
            db_session, _request(account, category)
        )
        entry = {
            "booking": "2026-02-10T00:00:00.000+0100",
            "valuation": "2026-02-10",
            "partnerAccount": {"iban": "AT611904300234573201"},
            "amount": {"value": "1500", "precision": "2"},
            "reference": "Beitrag Februar",
        }
        raw = json.dumps([entry])
        parsed = parse_george_json(GEORGE_BIC, raw)

        p4x_import_service.import_and_apply_filters(
            db_session, account, parsed.entries, json.loads(raw)
        )

        assert_hits_follow(db_session)
        imported = (
            db_session.query(P4xTransaction)
            .filter_by(iban="AT611904300234573201")
            .one()
        )
        assert (category_filter.id, imported.id) in stored_hits(db_session)

    def test_the_nightly_rebuild_repairs_a_stale_hit(self, db_session, setup):
        account, category, matching, _other = setup
        create_category_filter(db_session, _request(account, category))
        matching.subject = "Etwas anderes"
        db_session.commit()
        assert stored_hits(db_session) != expected_hits(db_session)

        apply_all_category_filters(db_session, truncate_first=True)

        assert_hits_follow(db_session)


def _partner_for_all(db, account) -> None:
    """Give every transaction a partner, so that no partner warning blocks the
    conversion to direct assignments."""
    member = Member(vorname="Max", nachname="Muster")
    db.add(member)
    db.flush()
    for iban in {
        tx.iban for tx in db.query(P4xTransaction).filter_by(p4x_account_id=account.id)
    }:
        db.add(
            P4xPartner(
                iban=iban,
                member_id=member.id,
                created_at=_now(),
                updated_at=_now(),
            )
        )
    db.commit()
