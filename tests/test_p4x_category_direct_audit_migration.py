"""Alembic revision b7d2f4a91c3e (audit columns of the direct categories): it adds
both columns with the shared trigger and can be undone.

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
    / "alembic/versions/b7d2f4a91c3e_p4x_category_directs_audit_columns.py"
)
TRIGGER = "p4x_category_directs_set_updated_at"


class _Revision:
    def __init__(self, db: Session) -> None:
        self._db = db
        spec = importlib.util.spec_from_file_location(
            "p4x_direct_audit_revision", REVISION_FILE
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

    def columns(self) -> set[str]:
        return set(
            self._db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'p4x_category_directs'"
                )
            ).scalars()
        )

    def has_trigger(self) -> bool:
        return bool(
            self._db.execute(
                text("SELECT 1 FROM pg_trigger WHERE tgname = :name"), {"name": TRIGGER}
            ).scalar()
        )


@pytest.fixture
def revision(db_session: Session) -> _Revision:
    return _Revision(db_session)


@pytest.fixture
def before(revision: _Revision) -> _Revision:
    """The schema as it was before the revision."""
    revision.downgrade()
    return revision


def test_the_schema_before_had_neither_columns_nor_trigger(before: _Revision) -> None:
    assert not {"created_at", "updated_at"} & before.columns()
    assert not before.has_trigger()


def test_the_upgrade_adds_both_columns_and_the_trigger(before: _Revision) -> None:
    before.upgrade()

    assert {"created_at", "updated_at"} <= before.columns()
    assert before.has_trigger()


def test_existing_rows_get_a_time(db_session: Session, before: _Revision) -> None:
    category = db_session.execute(
        text(
            "INSERT INTO p4x_categories (name, label, background_color, text_color, "
            "protected) VALUES ('a', 'a', '#000', '#fff', false) RETURNING id"
        )
    ).scalar_one()
    account = db_session.execute(
        text(
            "INSERT INTO p4x_accounts (iban, bic, label, init_date, init_balance) "
            "VALUES ('AT942011100005301947', 'GIBAATWWXXX', 'L', '2020-01-01', 0) "
            "RETURNING id"
        )
    ).scalar_one()
    transaction = db_session.execute(
        text(
            "INSERT INTO p4x_transactions (sha256_hash, booking, valuation, iban, "
            "amount, subject, p4x_account_id) VALUES ('h', '2026-01-01', "
            "'2026-01-01', 'AT1', 10, 's', :a) RETURNING id"
        ),
        {"a": account},
    ).scalar_one()
    db_session.execute(
        text(
            "INSERT INTO p4x_category_directs (p4x_transaction_id, p4x_category_id, "
            "amount) VALUES (:t, :c, 10)"
        ),
        {"t": transaction, "c": category},
    )

    # The sum check of the rows is deferred to the commit; a migration never
    # runs with such events pending, so fire them before altering the table.
    db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

    before.upgrade()

    missing = db_session.execute(
        text(
            "SELECT count(*) FROM p4x_category_directs "
            "WHERE created_at IS NULL OR updated_at IS NULL"
        )
    ).scalar_one()
    assert missing == 0


def test_the_downgrade_removes_them_again(before: _Revision) -> None:
    before.upgrade()

    before.downgrade()

    assert not {"created_at", "updated_at"} & before.columns()
    assert not before.has_trigger()


def test_the_upgrade_waits_at_most_a_few_seconds_for_a_lock(
    db_session: Session, before: _Revision
) -> None:
    db_session.execute(text("SET LOCAL lock_timeout = 0"))  # the downgrade set it

    before.upgrade()

    assert db_session.execute(text("SHOW lock_timeout")).scalar() == "5s"


def test_the_downgrade_waits_at_most_a_few_seconds_for_a_lock(
    db_session: Session, revision: _Revision
) -> None:
    db_session.execute(text("SET LOCAL lock_timeout = 0"))

    revision.downgrade()

    assert db_session.execute(text("SHOW lock_timeout")).scalar() == "5s"
