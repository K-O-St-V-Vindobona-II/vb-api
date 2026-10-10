"""Alembic revision e1b3c5d7f9a2 (direct categories add up to the transaction
amount): it refuses data that already breaks the rule, changes nothing in that
case, and can be undone.

The revision functions run inside the per-test transaction, so the schema
changes are rolled back with it. `downgrade()` first puts the schema into the
state before the revision."""

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

REVISION_FILE = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/e1b3c5d7f9a2_p4x_category_direct_sum.py"
)
TRIGGERS = ("p4x_category_directs_sum", "p4x_transactions_direct_sum")
FUNCTIONS = (
    "p4x_assert_category_direct_sum",
    "p4x_category_directs_check_sum",
    "p4x_transactions_check_direct_sum",
)


class _Revision:
    def __init__(self, db: Session) -> None:
        self._db = db
        spec = importlib.util.spec_from_file_location(
            "p4x_direct_sum_revision", REVISION_FILE
        )
        assert spec is not None
        assert spec.loader is not None
        self._module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self._module)

    def _run(self, step) -> None:
        self._db.flush()
        connection = self._db.connection()
        with Operations.context(MigrationContext.configure(connection)):
            step()

    def upgrade(self) -> None:
        self._run(self._module.upgrade)

    def downgrade(self) -> None:
        self._run(self._module.downgrade)

    def _count(self, sql: str, names: tuple[str, ...]) -> int:
        return self._db.execute(text(sql), {"names": list(names)}).scalar_one()

    def trigger_count(self) -> int:
        return self._count(
            "SELECT count(*) FROM pg_trigger WHERE tgname = ANY(:names)", TRIGGERS
        )

    def function_count(self) -> int:
        return self._count(
            "SELECT count(*) FROM pg_proc WHERE proname = ANY(:names)", FUNCTIONS
        )

    def triggers_are_deferred(self) -> bool:
        deferred = self._db.execute(
            text(
                "SELECT count(*) FROM pg_trigger WHERE tgname = ANY(:names) "
                "AND tgdeferrable AND tginitdeferred"
            ),
            {"names": list(TRIGGERS)},
        ).scalar_one()
        return deferred == len(TRIGGERS)


@pytest.fixture
def revision(db_session: Session) -> _Revision:
    return _Revision(db_session)


@pytest.fixture
def before(revision: _Revision) -> _Revision:
    """The schema as it was before the revision."""
    revision.downgrade()
    return revision


def _insert(db: Session, table: str, **columns: object) -> object:
    names = ", ".join(columns)
    values = ", ".join(f":{key}" for key in columns)
    return db.execute(
        text(f"INSERT INTO {table} ({names}) VALUES ({values}) RETURNING id"),  # noqa: S608
        columns,
    ).scalar_one()


def _transaction(db: Session, amount: str, suffix: str) -> object:
    account = db.execute(text("SELECT id FROM p4x_accounts LIMIT 1")).scalar()
    if account is None:
        account = _insert(
            db,
            "p4x_accounts",
            iban="AT00MIGTEST",
            bic="GIBAATWWXXX",
            label="Mig",
            init_date="2020-01-01",
            init_balance=0,
        )
    return _insert(
        db,
        "p4x_transactions",
        sha256_hash=f"direct_sum_mig_{suffix}",
        booking="2026-03-15",
        valuation="2026-03-15",
        iban="AT001",
        amount=amount,
        subject="Mig",
        p4x_account_id=account,
    )


def _category(db: Session, name: str) -> object:
    return _insert(
        db,
        "p4x_categories",
        name=name,
        label=name,
        background_color="#000",
        text_color="#fff",
        protected=False,
    )


def _direct(
    db: Session, transaction: object, category: object, amount: str, **columns: object
) -> object:
    return _insert(
        db,
        "p4x_category_directs",
        p4x_transaction_id=transaction,
        p4x_category_id=category,
        amount=amount,
        **columns,
    )


class TestUpgradeRefusesBadData:
    def test_rows_that_do_not_add_up(
        self, db_session: Session, before: _Revision
    ) -> None:
        bad = _transaction(db_session, "100.00", "bad")
        _direct(db_session, bad, _category(db_session, "mig.a"), "90.00")

        with pytest.raises(RuntimeError, match="do not add up") as exc:
            before.upgrade()

        assert str(bad) in str(exc.value)

    def test_only_the_offending_transaction_is_listed(
        self, db_session: Session, before: _Revision
    ) -> None:
        category = _category(db_session, "mig.a")
        good = _transaction(db_session, "100.00", "good")
        bad = _transaction(db_session, "50.00", "bad")
        _direct(db_session, good, category, "100.00")
        _direct(db_session, bad, category, "40.00")

        with pytest.raises(RuntimeError) as exc:
            before.upgrade()

        assert str(bad) in str(exc.value)
        assert str(good) not in str(exc.value)

    def test_correct_splits_and_unassigned_transactions_pass(
        self, db_session: Session, before: _Revision
    ) -> None:
        first = _category(db_session, "mig.a")
        second = _category(db_session, "mig.b")
        split = _transaction(db_session, "100.00", "split")
        _direct(db_session, split, first, "60.00")
        _direct(db_session, split, second, "40.00")
        _transaction(db_session, "20.00", "unassigned")

        before.upgrade()

        assert before.trigger_count() == len(TRIGGERS)

    def test_set_aside_rows_do_not_count(
        self, db_session: Session, before: _Revision
    ) -> None:
        category = _category(db_session, "mig.a")
        transaction = _transaction(db_session, "100.00", "aside")
        _direct(db_session, transaction, category, "100.00")
        _direct(
            db_session,
            transaction,
            _category(db_session, "mig.b"),
            "30.00",
            deleted_at="2026-01-01",
        )

        before.upgrade()

        assert before.trigger_count() == len(TRIGGERS)

    def test_nothing_is_changed_when_the_data_is_refused(
        self, db_session: Session, before: _Revision
    ) -> None:
        bad = _transaction(db_session, "100.00", "bad")
        _direct(db_session, bad, _category(db_session, "mig.a"), "90.00")

        with pytest.raises(RuntimeError):
            before.upgrade()

        assert before.trigger_count() == 0
        assert before.function_count() == 0

    def test_the_upgrade_runs_once_the_data_is_corrected(
        self, db_session: Session, before: _Revision
    ) -> None:
        bad = _transaction(db_session, "100.00", "bad")
        _direct(db_session, bad, _category(db_session, "mig.a"), "90.00")
        with pytest.raises(RuntimeError):
            before.upgrade()
        db_session.execute(text("UPDATE p4x_category_directs SET amount = 100"))

        before.upgrade()

        assert before.trigger_count() == len(TRIGGERS)


class TestSchema:
    def test_the_schema_before_had_no_check(self, before: _Revision) -> None:
        assert before.trigger_count() == 0
        assert before.function_count() == 0

    def test_the_triggers_exist_afterwards_and_are_deferred(
        self, before: _Revision
    ) -> None:
        before.upgrade()

        assert before.trigger_count() == len(TRIGGERS)
        assert before.function_count() == len(FUNCTIONS)
        assert before.triggers_are_deferred()

    def test_the_downgrade_removes_them_again(self, before: _Revision) -> None:
        before.upgrade()

        before.downgrade()

        assert before.trigger_count() == 0
        assert before.function_count() == 0

    def test_the_upgrade_waits_at_most_a_few_seconds_for_a_lock(
        self, db_session: Session, before: _Revision
    ) -> None:
        db_session.execute(text("SET LOCAL lock_timeout = 0"))  # the downgrade set it

        before.upgrade()

        assert db_session.execute(text("SHOW lock_timeout")).scalar() == "5s"

    def test_the_downgrade_waits_at_most_a_few_seconds_for_a_lock(
        self, db_session: Session, revision: _Revision
    ) -> None:
        db_session.execute(text("SET LOCAL lock_timeout = 0"))

        revision.downgrade()

        assert db_session.execute(text("SHOW lock_timeout")).scalar() == "5s"
