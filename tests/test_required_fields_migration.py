"""Alembic revision d8f2a4c6e0b1 (required comment text and badge name, description
lengths): it refuses data that would break a new rule, changes nothing in that
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
    / "alembic/versions/d8f2a4c6e0b1_required_comment_badge_description.py"
)

COMMENT_CHECK_BEFORE = (
    "CHECK (((content IS NULL) OR ((length((content)::text) >= 1) "
    "AND (length((content)::text) <= 1000))))"
)
DESCRIPTION_CHECKS = (
    "archive_dirs_description_check",
    "archive_files_description_check",
)


class _Revision:
    def __init__(self, db: Session) -> None:
        self._db = db
        spec = importlib.util.spec_from_file_location(
            "required_fields_revision", REVISION_FILE
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

    def check_definition(self, name: str) -> str | None:
        return self._db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"
            ),
            {"n": name},
        ).scalar()

    def is_required(self, table: str, column: str) -> bool:
        nullable = self._db.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar_one()
        return nullable == "NO"

    def untouched(self) -> bool:
        """True while the schema is still the one before the revision."""
        return (
            self.check_definition("archive_file_comments_content_check")
            == COMMENT_CHECK_BEFORE
            and not self.is_required("archive_file_comments", "content")
            and not self.is_required("badges", "name")
            and all(self.check_definition(name) is None for name in DESCRIPTION_CHECKS)
        )


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


def _file(db: Session, **columns: object) -> object:
    item = _insert(
        db,
        "archive_store_items",
        name="f",
        extension="txt",
        mime_type="text/plain",
        size=1,
        sha256_hash="e" * 64,
    )
    return _insert(db, "archive_files", archive_store_item_id=item, **columns)


def _comment(db: Session, file_id: object, content: str | None) -> object:
    return _insert(
        db, "archive_file_comments", archive_file_id=file_id, content=content
    )


class TestUpgradeRefusesBadData:
    def test_a_comment_without_text(
        self, db_session: Session, before: _Revision
    ) -> None:
        row = _comment(db_session, _file(db_session), None)

        with pytest.raises(RuntimeError, match="Comments without text") as exc:
            before.upgrade()

        assert str(row) in str(exc.value)

    @pytest.mark.parametrize("content", ["a", "abcd"])
    def test_a_comment_shorter_than_the_api_minimum(
        self, db_session: Session, before: _Revision, content: str
    ) -> None:
        row = _comment(db_session, _file(db_session), content)

        with pytest.raises(RuntimeError, match="shorter than 5") as exc:
            before.upgrade()

        assert str(row) in str(exc.value)

    def test_a_badge_without_a_name(
        self, db_session: Session, before: _Revision
    ) -> None:
        row = _insert(db_session, "badges", name=None)

        with pytest.raises(RuntimeError, match="Badges without a name") as exc:
            before.upgrade()

        assert str(row) in str(exc.value)

    def test_a_long_directory_description(
        self, db_session: Session, before: _Revision
    ) -> None:
        row = _insert(db_session, "archive_dirs", name="Dir1x", description="x" * 129)

        with pytest.raises(RuntimeError, match="Directories") as exc:
            before.upgrade()

        assert str(row) in str(exc.value)

    def test_a_long_file_description(
        self, db_session: Session, before: _Revision
    ) -> None:
        row = _file(db_session, description="x" * 129)

        with pytest.raises(RuntimeError, match="Files") as exc:
            before.upgrade()

        assert str(row) in str(exc.value)

    def test_values_at_the_limits_pass(
        self, db_session: Session, before: _Revision
    ) -> None:
        file_id = _file(db_session, description="ä" * 128)
        _comment(db_session, file_id, "ä" * 5)
        _comment(db_session, file_id, "ä" * 1000)
        _insert(db_session, "archive_dirs", name="Dir1x", description="ä" * 128)
        _insert(db_session, "badges", name="Ehrenmitglied")

        before.upgrade()

        assert before.is_required("badges", "name")

    @pytest.mark.parametrize("bad", ["comment", "badge", "description"])
    def test_nothing_is_changed_when_the_data_is_refused(
        self, db_session: Session, before: _Revision, bad: str
    ) -> None:
        if bad == "comment":
            _comment(db_session, _file(db_session), None)
        if bad == "badge":
            _insert(db_session, "badges", name=None)
        if bad == "description":
            _insert(db_session, "archive_dirs", name="Dir1x", description="x" * 129)

        with pytest.raises(RuntimeError):
            before.upgrade()

        assert before.untouched()

    def test_the_upgrade_runs_once_the_data_is_corrected(
        self, db_session: Session, before: _Revision
    ) -> None:
        _insert(db_session, "badges", name=None)
        with pytest.raises(RuntimeError):
            before.upgrade()
        db_session.execute(text("UPDATE badges SET name = 'Name'"))

        before.upgrade()

        assert before.is_required("badges", "name")


class TestSchema:
    def test_the_schema_before_had_none_of_the_rules(self, before: _Revision) -> None:
        assert before.untouched()

    def test_the_rules_exist_afterwards(self, before: _Revision) -> None:
        before.upgrade()

        assert before.is_required("archive_file_comments", "content")
        assert before.is_required("badges", "name")
        assert "5" in (
            before.check_definition("archive_file_comments_content_check") or ""
        )
        for name in DESCRIPTION_CHECKS:
            assert "128" in (before.check_definition(name) or "")

    def test_the_downgrade_removes_them_again(self, before: _Revision) -> None:
        before.upgrade()

        before.downgrade()

        assert before.untouched()

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
