"""Alembic revision 30f721a1412a (order becomes sort_order on orgs, roles,
states and badges): the data and the non-negative rule survive the rename in
both directions, the constraints are renamed with the column, and the models
declare the new names.

The revision functions run inside the per-test transaction, so the schema
changes are rolled back with it. `downgrade()` first puts the schema into the
state before the revision."""

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import CheckConstraint, text
from sqlalchemy.exc import IntegrityError

from app.models.badge import Badge
from app.models.org import Org
from app.models.role import Role
from app.models.state import State

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

REVISION_FILE = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/30f721a1412a_reference_tables_order_to_sort_order.py"
)
MODELS = {"orgs": Org, "roles": Role, "states": State, "badges": Badge}
TABLES = pytest.mark.parametrize("table", list(MODELS))


class _Revision:
    def __init__(self, db: Session) -> None:
        self._db = db
        spec = importlib.util.spec_from_file_location(
            "sort_order_rename_revision", REVISION_FILE
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

    def columns(self, table: str) -> set[str]:
        return set(
            self._db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :t"
                ),
                {"t": table},
            ).scalars()
        )

    def check_definition(self, name: str) -> str | None:
        return self._db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"
            ),
            {"n": name},
        ).scalar_one_or_none()


@pytest.fixture
def revision(db_session: Session) -> _Revision:
    return _Revision(db_session)


@pytest.fixture
def before(revision: _Revision) -> _Revision:
    """The schema as it was before the revision: the column is still `order`."""
    revision.downgrade()
    return revision


def _insert_row(db: Session, table: str, position: str) -> None:
    key = "name" if table == "badges" else "id"
    db.execute(
        text(
            f'INSERT INTO {table} ({key}, "order") VALUES (:key, {position})'  # noqa: S608
        ),
        {"key": "check-row"},
    )


class TestSchema:
    @TABLES
    def test_the_column_is_called_order_before(
        self, before: _Revision, table: str
    ) -> None:
        assert "order" in before.columns(table)
        assert "sort_order" not in before.columns(table)
        assert before.check_definition(f"{table}_order_check") is not None

    @TABLES
    def test_the_column_and_the_constraint_are_renamed(
        self, before: _Revision, table: str
    ) -> None:
        before.upgrade()

        assert "sort_order" in before.columns(table)
        assert "order" not in before.columns(table)
        assert before.check_definition(f"{table}_order_check") is None
        definition = before.check_definition(f"{table}_sort_order_check")
        assert definition is not None
        assert "sort_order" in definition
        assert '"order"' not in definition

    @TABLES
    def test_the_current_schema_matches_the_revision(
        self, revision: _Revision, table: str
    ) -> None:
        assert "sort_order" in revision.columns(table)
        assert "order" not in revision.columns(table)
        assert revision.check_definition(f"{table}_sort_order_check") is not None

    @TABLES
    def test_the_downgrade_restores_the_old_names(
        self, before: _Revision, table: str
    ) -> None:
        before.upgrade()

        before.downgrade()

        assert "order" in before.columns(table)
        assert "sort_order" not in before.columns(table)
        assert before.check_definition(f"{table}_order_check") is not None
        assert before.check_definition(f"{table}_sort_order_check") is None

    @TABLES
    def test_the_models_declare_the_new_names(
        self, revision: _Revision, table: str
    ) -> None:
        model_table = MODELS[table].__table__
        checks = {
            c.name: str(c.sqltext)
            for c in model_table.constraints
            if isinstance(c, CheckConstraint)
        }

        assert "sort_order" in model_table.columns
        assert "order" not in model_table.columns
        assert checks == {
            f"{table}_sort_order_check": "sort_order IS NULL OR sort_order >= 0"
        }


class TestData:
    @TABLES
    def test_the_position_survives_the_rename(
        self, db_session: Session, before: _Revision, table: str
    ) -> None:
        _insert_row(db_session, table, "7")

        before.upgrade()

        assert (
            db_session.execute(
                text(f"SELECT sort_order FROM {table}")  # noqa: S608
            ).scalar_one()
            == 7
        )

    @TABLES
    def test_the_position_survives_the_downgrade(
        self, db_session: Session, revision: _Revision, table: str
    ) -> None:
        key = "name" if table == "badges" else "id"
        db_session.execute(
            text(f"INSERT INTO {table} ({key}, sort_order) VALUES ('check-row', 4)")  # noqa: S608
        )

        revision.downgrade()

        assert (
            db_session.execute(
                text(f'SELECT "order" FROM {table}')  # noqa: S608
            ).scalar_one()
            == 4
        )

    @TABLES
    def test_a_negative_position_is_still_refused(
        self, db_session: Session, before: _Revision, table: str
    ) -> None:
        before.upgrade()
        key = "name" if table == "badges" else "id"

        with pytest.raises(IntegrityError, match=f"{table}_sort_order_check"):
            db_session.execute(
                text(f"INSERT INTO {table} ({key}, sort_order) VALUES ('x', -1)")  # noqa: S608
            )

    @TABLES
    def test_a_missing_position_is_still_allowed(
        self, db_session: Session, before: _Revision, table: str
    ) -> None:
        before.upgrade()
        key = "name" if table == "badges" else "id"

        db_session.execute(
            text(f"INSERT INTO {table} ({key}, sort_order) VALUES ('x', NULL)")  # noqa: S608
        )


class TestLockTimeout:
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
