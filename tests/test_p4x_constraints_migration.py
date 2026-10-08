"""Alembic revision 9b4c2d6e1a73 (unique active account IBAN, filter criterion,
fees start on the 1st): it refuses data that would break a new rule, changes
nothing in that case, and can be undone.

The revision functions run inside the per-test transaction, so the schema
changes are rolled back with it. `downgrade()` first puts the schema into the
state before the revision."""

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.models.enums import SubjectMode
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_fee import P4xFee

REVISION_FILE = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/9b4c2d6e1a73_p4x_account_filter_fee_constraints.py"
)
IBAN = "AT942011100005301947"
IBAN_WITH_BLANKS = "AT94 2011 1000 0530 1947"


def _load_revision():
    spec = importlib.util.spec_from_file_location(
        "p4x_constraints_revision", REVISION_FILE
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Revision:
    def __init__(self, db_session):
        self._db = db_session
        self._module = _load_revision()

    def _run(self, step):
        self._db.flush()
        connection = self._db.connection()
        with Operations.context(MigrationContext.configure(connection)):
            step()

    def upgrade(self):
        self._run(self._module.upgrade)

    def downgrade(self):
        self._run(self._module.downgrade)

    def has_constraint(self, name):
        return bool(
            self._db.execute(
                text("SELECT 1 FROM pg_constraint WHERE conname = :name"),
                {"name": name},
            ).scalar()
        )

    def has_index(self, name):
        return bool(
            self._db.execute(
                text("SELECT 1 FROM pg_indexes WHERE indexname = :name"), {"name": name}
            ).scalar()
        )


@pytest.fixture
def revision(db_session):
    return _Revision(db_session)


@pytest.fixture
def before(revision):
    """The schema as it was before the revision."""
    revision.downgrade()
    return revision


def _filter(db_session, name, **criteria):
    account = db_session.query(P4xAccount).first() or P4xAccount(
        iban=IBAN, label="a", init_balance=0
    )
    category = P4xCategory(
        name=f"c-{name}", label="c", background_color="#000000", text_color="#ffffff"
    )
    db_session.add_all([account, category])
    db_session.flush()
    db_session.add(
        P4xCategoryFilter(
            name=name,
            p4x_account_id=account.id,
            p4x_category_id=category.id,
            subject_mode=SubjectMode.CONTAINS,
            **criteria,
        )
    )
    db_session.flush()


class TestUpgradeRefusesBadData:
    def test_two_active_accounts_with_the_same_iban(self, db_session, before):
        db_session.add_all(
            [
                P4xAccount(iban=IBAN, label="a", init_balance=0),
                P4xAccount(iban=IBAN_WITH_BLANKS, label="b", init_balance=0),
            ]
        )

        with pytest.raises(
            RuntimeError, match="Active accounts with the same IBAN"
        ) as exc:
            before.upgrade()

        assert IBAN in str(exc.value)

    def test_a_filter_without_any_criterion(self, db_session, before):
        _filter(db_session, "matches-everything")

        with pytest.raises(RuntimeError, match="matches-everything"):
            before.upgrade()

    def test_a_fee_that_starts_in_the_middle_of_a_month(self, db_session, before):
        db_session.add(
            P4xFee(start=date(2026, 1, 15), fee=Decimal(20), protected=False)
        )

        with pytest.raises(RuntimeError, match="2026-01-15"):
            before.upgrade()

    @pytest.mark.parametrize("bad", ["account", "filter", "fee"])
    def test_nothing_is_changed_when_the_data_is_refused(self, db_session, before, bad):
        if bad == "account":
            db_session.add_all(
                [
                    P4xAccount(iban=IBAN, label="a", init_balance=0),
                    P4xAccount(iban=IBAN_WITH_BLANKS, label="b", init_balance=0),
                ]
            )
        if bad == "filter":
            _filter(db_session, "matches-everything")
        if bad == "fee":
            db_session.add(
                P4xFee(start=date(2026, 1, 15), fee=Decimal(20), protected=False)
            )

        with pytest.raises(RuntimeError):
            before.upgrade()

        assert before.has_constraint("p4x_accounts_iban_key")
        assert not before.has_index("p4x_accounts_iban_active_key")
        assert not before.has_constraint("p4x_category_filters_criterion_check")
        assert not before.has_constraint("p4x_fees_start_first_of_month_check")

    def test_the_upgrade_runs_once_the_data_is_corrected(self, db_session, before):
        _filter(db_session, "matches-everything")
        with pytest.raises(RuntimeError):
            before.upgrade()
        db_session.query(P4xCategoryFilter).update({"subject": "Beitrag"})

        before.upgrade()

        assert before.has_index("p4x_accounts_iban_active_key")


class TestUpgradeOnCleanData:
    def test_a_deleted_duplicate_does_not_block_the_upgrade(self, db_session, before):
        db_session.add_all(
            [
                P4xAccount(iban=IBAN, label="a", init_balance=0),
                P4xAccount(
                    iban=IBAN_WITH_BLANKS,
                    label="b",
                    init_balance=0,
                    deleted_at=text("now()"),
                ),
            ]
        )

        before.upgrade()

        assert before.has_index("p4x_accounts_iban_active_key")

    def test_the_rules_exist_afterwards(self, before):
        before.upgrade()

        assert not before.has_constraint("p4x_accounts_iban_key")
        assert before.has_index("p4x_accounts_iban_active_key")
        assert before.has_constraint("p4x_category_filters_criterion_check")
        assert before.has_constraint("p4x_fees_start_first_of_month_check")

    def test_existing_ibans_keep_their_spelling(self, db_session, before):
        db_session.add(P4xAccount(iban=IBAN_WITH_BLANKS, label="a", init_balance=0))
        db_session.flush()

        before.upgrade()

        stored = db_session.query(P4xAccount.iban).scalar()
        assert stored == IBAN_WITH_BLANKS

    def test_ddl_waits_at_most_five_seconds_for_a_lock(self, db_session, before):
        # The fixture's downgrade set the limit already: start from none.
        db_session.execute(text("SET LOCAL lock_timeout = 0"))

        before.upgrade()

        assert db_session.execute(text("SHOW lock_timeout")).scalar() == "5s"


class TestModelsDeclareWhatTheRevisionCreates:
    """The tests build the schema from the revisions, so nothing else compares
    the models with it: a rule dropped from a model would go unnoticed and the
    next generated revision would drop it from the database."""

    def test_the_account_iban_is_unique_among_active_accounts_only(self):
        table = P4xAccount.__table__
        index = next(
            i for i in table.indexes if i.name == "p4x_accounts_iban_active_key"
        )

        assert index.unique
        assert str(index.dialect_options["postgresql"]["where"]) == "deleted_at IS NULL"
        assert "replace(iban" in str(index.expressions[0])
        assert not table.c.iban.unique
        assert not any(c.name == "p4x_accounts_iban_key" for c in table.constraints)

    def test_the_filter_and_fee_rules_are_declared(self):
        filter_rules = {c.name for c in P4xCategoryFilter.__table__.constraints}
        fee_rules = {c.name for c in P4xFee.__table__.constraints}

        assert "p4x_category_filters_criterion_check" in filter_rules
        assert "p4x_fees_start_first_of_month_check" in fee_rules


class TestDowngrade:
    def test_ddl_waits_at_most_five_seconds_for_a_lock(self, db_session, revision):
        db_session.execute(text("SET LOCAL lock_timeout = 0"))

        revision.downgrade()

        assert db_session.execute(text("SHOW lock_timeout")).scalar() == "5s"

    def test_it_restores_the_plain_unique_constraint(self, revision):
        revision.downgrade()

        assert revision.has_constraint("p4x_accounts_iban_key")
        assert not revision.has_index("p4x_accounts_iban_active_key")
        assert not revision.has_constraint("p4x_category_filters_criterion_check")
        assert not revision.has_constraint("p4x_fees_start_first_of_month_check")

    def test_it_stops_when_an_iban_was_used_again_after_a_deletion(
        self, db_session, revision
    ):
        db_session.add_all(
            [
                P4xAccount(
                    iban=IBAN, label="old", init_balance=0, deleted_at=text("now()")
                ),
                P4xAccount(iban=IBAN, label="new", init_balance=0),
            ]
        )

        with pytest.raises(
            RuntimeError, match="IBANs used by more than one account"
        ) as exc:
            revision.downgrade()

        assert IBAN in str(exc.value)
        assert revision.has_index("p4x_accounts_iban_active_key")
        assert not revision.has_constraint("p4x_accounts_iban_key")
