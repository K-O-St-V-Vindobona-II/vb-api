"""A direct category assignment records when it was made and last changed; the
change time is kept by the shared trigger, never by Python."""

from datetime import UTC, datetime

from sqlalchemy import text

from app.models.p4x_category_direct import P4xCategoryDirect
from tests.p4x.test_p4x_category_direct_sum import (
    _make_account,
    _make_category,
    _make_direct,
    _make_transaction,
)


def _direct(db) -> P4xCategoryDirect:
    account = _make_account(db)
    category = _make_category(db, "audit.cat")
    tx = _make_transaction(db, account)
    direct = _make_direct(db, tx, category, "100.00")
    db.commit()
    return direct


class TestAuditColumns:
    def test_both_times_are_set_by_the_database_on_insert(self, db_session):
        before = datetime.now(UTC)

        direct = _direct(db_session)
        db_session.refresh(direct)

        assert direct.created_at is not None
        assert direct.updated_at is not None
        assert direct.created_at >= before.replace(microsecond=0)

    def test_an_update_moves_the_change_time_through_the_trigger(self, db_session):
        direct = _direct(db_session)
        db_session.refresh(direct)
        inserted_at = direct.updated_at

        direct.deleted_at = datetime.now(UTC)
        db_session.commit()
        db_session.refresh(direct)

        assert direct.updated_at > inserted_at
        assert direct.created_at < direct.updated_at

    def test_the_change_time_is_not_taken_from_the_statement(self, db_session):
        direct = _direct(db_session)
        db_session.execute(
            text(
                "UPDATE p4x_category_directs SET amount = amount, "
                "updated_at = '2000-01-01' WHERE id = :id"
            ),
            {"id": direct.id},
        )
        db_session.refresh(direct)

        assert direct.updated_at.year >= 2026

    def test_a_row_inserted_with_sql_gets_both_times(self, db_session):
        direct = _direct(db_session)
        copy_id = db_session.execute(
            text(
                "INSERT INTO p4x_category_directs "
                "(p4x_transaction_id, p4x_category_id, amount, deleted_at) "
                "SELECT p4x_transaction_id, p4x_category_id, amount, now() "
                "FROM p4x_category_directs WHERE id = :id RETURNING id"
            ),
            {"id": direct.id},
        ).scalar_one()

        row = db_session.execute(
            text(
                "SELECT created_at IS NOT NULL, updated_at IS NOT NULL "
                "FROM p4x_category_directs WHERE id = :id"
            ),
            {"id": copy_id},
        ).one()

        assert tuple(row) == (True, True)


def test_the_model_declares_both_columns():
    columns = P4xCategoryDirect.__table__.c

    assert columns.created_at.nullable is False
    assert columns.updated_at.nullable is False
    assert columns.created_at.server_default is not None
    assert columns.updated_at.server_default is not None
