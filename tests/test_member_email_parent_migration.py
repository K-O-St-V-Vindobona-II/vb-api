"""Alembic revision 7a3e91c4d5f8 (member addresses unique ignoring case, no
parent cycles): it refuses data that would break a new rule, changes nothing
in that case, and can be undone.

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

from app.models.org import Org
from app.models.state import State

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

REVISION_FILE = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/7a3e91c4d5f8_member_email_case_parent_cycle.py"
)


class _Revision:
    def __init__(self, db: Session) -> None:
        self._db = db
        spec = importlib.util.spec_from_file_location(
            "member_email_parent_revision", REVISION_FILE
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

    def _exists(self, sql: str, name: str) -> bool:
        return bool(self._db.execute(text(sql), {"name": name}).scalar())

    def has_index(self, name: str) -> bool:
        return self._exists("SELECT 1 FROM pg_indexes WHERE indexname = :name", name)

    def has_constraint(self, name: str) -> bool:
        return self._exists("SELECT 1 FROM pg_constraint WHERE conname = :name", name)

    def has_trigger(self, name: str) -> bool:
        return self._exists("SELECT 1 FROM pg_trigger WHERE tgname = :name", name)

    def has_function(self, name: str) -> bool:
        return self._exists("SELECT 1 FROM pg_proc WHERE proname = :name", name)

    def email_index_is_unique(self) -> bool:
        definition = self._db.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_members_email'")
        ).scalar_one()
        return "UNIQUE" in definition


@pytest.fixture
def revision(db_session: Session) -> _Revision:
    return _Revision(db_session)


@pytest.fixture
def before(revision: _Revision, db_session: Session) -> _Revision:
    """The schema as it was before the revision, with an organisation and a state."""
    revision.downgrade()
    db_session.add_all(
        [
            Org(id="vbw", label="V", sort_order=1),
            State(id="up", label="U", sort_order=1),
        ]
    )
    db_session.flush()
    return revision


def _member(db: Session, name: str, **columns: object) -> object:
    columns = {
        "vorname": name,
        "nachname": "X",
        "org_id": "vbw",
        "state_id": "up",
    } | columns
    names = ", ".join(columns)
    values = ", ".join(f":{key}" for key in columns)
    return db.execute(
        text(f"INSERT INTO members ({names}) VALUES ({values}) RETURNING id"),  # noqa: S608
        columns,
    ).scalar_one()


def _link(db: Session, child: object, parent: object) -> None:
    db.execute(
        text("UPDATE members SET parent_id = :parent WHERE id = :child"),
        {"parent": parent, "child": child},
    )


class TestUpgradeRefusesBadData:
    def test_two_addresses_that_differ_only_in_case(
        self, db_session: Session, before: _Revision
    ) -> None:
        _member(db_session, "A", email="Victim@x.at")
        _member(db_session, "B", email="victim@x.at")

        with pytest.raises(RuntimeError, match="differ only in case") as exc:
            before.upgrade()

        assert "victim@x.at" in str(exc.value)

    def test_members_without_an_address_do_not_clash(
        self, db_session: Session, before: _Revision
    ) -> None:
        _member(db_session, "A")
        _member(db_session, "B")

        before.upgrade()

        assert before.has_index("members_email_lower_key")

    def test_a_stored_parent_cycle(
        self, db_session: Session, before: _Revision
    ) -> None:
        first = _member(db_session, "A")
        second = _member(db_session, "B")
        _link(db_session, first, second)
        _link(db_session, second, first)

        with pytest.raises(RuntimeError, match="parent cycle") as exc:
            before.upgrade()

        assert str(first) in str(exc.value)
        assert str(second) in str(exc.value)

    @pytest.mark.parametrize("length", [3, 5, 12])
    def test_a_longer_stored_parent_cycle(
        self, db_session: Session, before: _Revision, length: int
    ) -> None:
        ids = [_member(db_session, f"m{i}") for i in range(length)]
        for child, parent in zip(ids, ids[1:] + ids[:1], strict=True):
            _link(db_session, child, parent)

        with pytest.raises(RuntimeError, match="parent cycle") as exc:
            before.upgrade()

        assert all(str(member_id) in str(exc.value) for member_id in ids)

    def test_a_member_that_is_its_own_parent(
        self, db_session: Session, before: _Revision
    ) -> None:
        member = _member(db_session, "A")
        _link(db_session, member, member)

        with pytest.raises(RuntimeError, match="parent cycle"):
            before.upgrade()

    def test_a_long_chain_without_a_cycle_is_accepted(
        self, db_session: Session, before: _Revision
    ) -> None:
        previous = _member(db_session, "root")
        for i in range(20):
            current = _member(db_session, f"c{i}")
            _link(db_session, current, previous)
            previous = current

        before.upgrade()

        assert before.has_trigger("members_parent_cycle")

    @pytest.mark.parametrize("bad", ["email", "cycle"])
    def test_nothing_is_changed_when_the_data_is_refused(
        self, db_session: Session, before: _Revision, bad: str
    ) -> None:
        if bad == "email":
            _member(db_session, "A", email="Victim@x.at")
            _member(db_session, "B", email="victim@x.at")
        if bad == "cycle":
            first = _member(db_session, "A")
            second = _member(db_session, "B")
            _link(db_session, first, second)
            _link(db_session, second, first)

        with pytest.raises(RuntimeError):
            before.upgrade()

        assert before.email_index_is_unique()
        assert not before.has_index("members_email_lower_key")
        assert not before.has_constraint("members_parent_not_self_check")
        assert not before.has_trigger("members_parent_cycle")
        assert not before.has_function("members_prevent_parent_cycle")

    def test_the_upgrade_runs_once_the_data_is_corrected(
        self, db_session: Session, before: _Revision
    ) -> None:
        _member(db_session, "A", email="Victim@x.at")
        second = _member(db_session, "B", email="victim@x.at")
        with pytest.raises(RuntimeError):
            before.upgrade()
        db_session.execute(
            text("UPDATE members SET email = 'other@x.at' WHERE id = :id"),
            {"id": second},
        )

        before.upgrade()

        assert before.has_index("members_email_lower_key")


class TestSchema:
    def test_the_schema_before_had_none_of_the_rules(self, before: _Revision) -> None:
        assert before.email_index_is_unique()
        assert not before.has_index("members_email_lower_key")
        assert not before.has_constraint("members_parent_not_self_check")
        assert not before.has_trigger("members_parent_cycle")

    def test_the_rules_exist_afterwards(self, before: _Revision) -> None:
        before.upgrade()

        assert not before.email_index_is_unique()
        assert before.has_index("members_email_lower_key")
        assert before.has_constraint("members_parent_not_self_check")
        assert before.has_trigger("members_parent_cycle")
        assert before.has_function("members_prevent_parent_cycle")

    def test_the_downgrade_removes_them_again(self, before: _Revision) -> None:
        before.upgrade()

        before.downgrade()

        assert before.email_index_is_unique()
        assert not before.has_index("members_email_lower_key")
        assert not before.has_constraint("members_parent_not_self_check")
        assert not before.has_trigger("members_parent_cycle")
        assert not before.has_function("members_prevent_parent_cycle")

    def test_the_upgrade_waits_at_most_a_few_seconds_for_a_lock(
        self, db_session: Session, before: _Revision
    ) -> None:
        db_session.execute(text("SET LOCAL lock_timeout = 0"))  # the downgrade set it

        before.upgrade()

        assert db_session.execute(text("SHOW lock_timeout")).scalar() == "5s"
