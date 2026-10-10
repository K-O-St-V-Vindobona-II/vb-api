"""Alembic revision d5f7b9c1e3a4 (drops the unused p4x_summary_orders table):
the upgrade removes the table with its rows, and the downgrade recreates the
same structure, empty, so the migration chain can be walked in both directions.

The revision functions run inside the per-test transaction, so the schema
changes are rolled back with it. `downgrade()` first puts the schema into the
state before the revision."""

import importlib.util
import uuid
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

REVISION_FILE = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/d5f7b9c1e3a4_drop_unused_p4x_summary_orders.py"
)
TABLE = "p4x_summary_orders"
EXPECTED_COLUMNS = {
    "id": ("uuid", "NO", "uuidv7()"),
    "ordered_by": ("uuid", "NO", None),
    "email": ("character varying", "NO", None),
    "summary_start": ("date", "NO", None),
    "summary_end": ("date", "NO", None),
    "pid": ("character varying", "YES", None),
    "started_at": ("timestamp with time zone", "YES", None),
    "finished_at": ("timestamp with time zone", "YES", None),
    "finished_ok": ("boolean", "NO", None),
    "created_at": ("timestamp with time zone", "NO", "now()"),
    "updated_at": ("timestamp with time zone", "YES", None),
}
EXPECTED_CONSTRAINTS = {
    "p4x_summary_orders_pkey": "PRIMARY KEY (id)",
    "p4x_summary_orders_ordered_by_fkey": (
        "FOREIGN KEY (ordered_by) REFERENCES members(id) "
        "ON UPDATE CASCADE ON DELETE CASCADE"
    ),
    "p4x_summary_orders_summary_start_end_check": (
        "CHECK ((summary_end >= summary_start))"
    ),
}


class _Revision:
    def __init__(self, db: Session) -> None:
        self._db = db
        spec = importlib.util.spec_from_file_location(
            "summary_orders_drop_revision", REVISION_FILE
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

    def exists(self) -> bool:
        return (
            self._db.execute(text(f"SELECT to_regclass('public.{TABLE}')")).scalar()
            is not None
        )

    def columns(self) -> dict[str, tuple[str, str, str | None]]:
        rows = self._db.execute(
            text(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_name = :t"
            ),
            {"t": TABLE},
        )
        return {row[0]: (row[1], row[2], row[3]) for row in rows}

    def constraints(self) -> dict[str, str]:
        rows = self._db.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = to_regclass(:t) AND contype <> 'n'"
            ),
            {"t": f"public.{TABLE}"},
        )
        return {row[0]: row[1] for row in rows}

    def index_names(self) -> set[str]:
        return set(
            self._db.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = :t"),
                {"t": TABLE},
            ).scalars()
        )

    def trigger_functions(self) -> dict[str, str]:
        rows = self._db.execute(
            text(
                "SELECT t.tgname, p.proname FROM pg_trigger t "
                "JOIN pg_proc p ON p.oid = t.tgfoid "
                "WHERE t.tgrelid = to_regclass(:t) AND NOT t.tgisinternal"
            ),
            {"t": f"public.{TABLE}"},
        )
        return {row[0]: row[1] for row in rows}


@pytest.fixture
def revision(db_session: Session) -> _Revision:
    return _Revision(db_session)


@pytest.fixture
def before(revision: _Revision) -> _Revision:
    """The schema as it was before the revision: the table exists, empty."""
    revision.downgrade()
    return revision


def _member(db: Session) -> uuid.UUID:
    return db.execute(
        text("INSERT INTO members (vorname, nachname) VALUES ('A', 'B') RETURNING id")
    ).scalar_one()


def _order(db: Session, member: uuid.UUID, start: date, end: date) -> uuid.UUID:
    return db.execute(
        text(
            f"INSERT INTO {TABLE} "  # noqa: S608
            "(ordered_by, email, summary_start, summary_end, finished_ok) "
            "VALUES (:m, 'x@example.org', :s, :e, false) RETURNING id"
        ),
        {"m": member, "s": start, "e": end},
    ).scalar_one()


class TestUpgrade:
    def test_the_current_schema_has_no_such_table(self, revision: _Revision) -> None:
        assert not revision.exists()

    def test_the_table_exists_before(self, before: _Revision) -> None:
        assert before.exists()

    def test_the_table_and_its_rows_are_gone_afterwards(
        self, db_session: Session, before: _Revision
    ) -> None:
        _order(db_session, _member(db_session), date(2025, 1, 1), date(2025, 2, 1))

        before.upgrade()

        assert not before.exists()

    def test_the_members_survive_the_drop(
        self, db_session: Session, before: _Revision
    ) -> None:
        member = _member(db_session)
        _order(db_session, member, date(2025, 1, 1), date(2025, 2, 1))

        before.upgrade()

        assert (
            db_session.execute(
                text("SELECT count(*) FROM members WHERE id = :m"), {"m": member}
            ).scalar_one()
            == 1
        )


class TestDowngradeRestoresTheStructure:
    def test_the_columns(self, before: _Revision) -> None:
        assert before.columns() == EXPECTED_COLUMNS

    def test_the_constraints(self, before: _Revision) -> None:
        assert before.constraints() == EXPECTED_CONSTRAINTS

    def test_the_indexes(self, before: _Revision) -> None:
        assert before.index_names() == {
            "p4x_summary_orders_pkey",
            "ix_p4x_summary_orders_ordered_by",
        }

    def test_the_updated_at_trigger(self, before: _Revision) -> None:
        assert before.trigger_functions() == {
            "p4x_summary_orders_set_updated_at": "set_updated_at"
        }

    def test_the_restored_table_is_empty(
        self, db_session: Session, before: _Revision
    ) -> None:
        assert (
            db_session.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one()  # noqa: S608
            == 0
        )

    def test_a_second_round_trip_gives_the_same_structure(
        self, before: _Revision
    ) -> None:
        before.upgrade()

        before.downgrade()

        assert before.columns() == EXPECTED_COLUMNS
        assert before.constraints() == EXPECTED_CONSTRAINTS


class TestRestoredTableBehaves:
    def test_the_id_is_generated_by_the_database(
        self, db_session: Session, before: _Revision
    ) -> None:
        order = _order(
            db_session, _member(db_session), date(2025, 1, 1), date(2025, 2, 1)
        )

        assert isinstance(order, uuid.UUID)
        assert order.version == 7

    def test_an_end_before_the_start_is_refused(
        self, db_session: Session, before: _Revision
    ) -> None:
        member = _member(db_session)

        with (
            pytest.raises(IntegrityError, match="summary_start_end_check"),
            db_session.begin_nested(),
        ):
            _order(db_session, member, date(2025, 2, 1), date(2025, 1, 1))

    def test_an_unknown_member_is_refused(
        self, db_session: Session, before: _Revision
    ) -> None:
        with (
            pytest.raises(IntegrityError, match="ordered_by_fkey"),
            db_session.begin_nested(),
        ):
            _order(db_session, uuid.uuid4(), date(2025, 1, 1), date(2025, 2, 1))

    def test_deleting_the_member_deletes_its_orders(
        self, db_session: Session, before: _Revision
    ) -> None:
        member = _member(db_session)
        _order(db_session, member, date(2025, 1, 1), date(2025, 2, 1))

        db_session.execute(text("DELETE FROM members WHERE id = :m"), {"m": member})

        assert (
            db_session.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one()  # noqa: S608
            == 0
        )

    def test_updated_at_is_set_on_update(
        self, db_session: Session, before: _Revision
    ) -> None:
        order = _order(
            db_session, _member(db_session), date(2025, 1, 1), date(2025, 2, 1)
        )

        db_session.execute(
            text(f"UPDATE {TABLE} SET pid = '1' WHERE id = :i"),  # noqa: S608
            {"i": order},
        )

        assert (
            db_session.execute(
                text(f"SELECT updated_at FROM {TABLE} WHERE id = :i"),  # noqa: S608
                {"i": order},
            ).scalar_one()
            is not None
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
