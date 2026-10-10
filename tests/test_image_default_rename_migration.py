"""Alembic revision 3871dffffdd0 (standesdb_images.default becomes is_default):
the data and the one-default-per-owner rule survive the rename in both
directions, and the model declares the new name.

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
from sqlalchemy.exc import IntegrityError

from app.models.standesdb_image import StandesdbImage

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

REVISION_FILE = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/3871dffffdd0_standesdb_images_default_to_is_default.py"
)
DEFAULT_INDEXES = (
    "standesdb_images_member_default_uniq",
    "standesdb_images_contact_default_uniq",
)


class _Revision:
    def __init__(self, db: Session) -> None:
        self._db = db
        spec = importlib.util.spec_from_file_location(
            "image_default_rename_revision", REVISION_FILE
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
                    "WHERE table_name = 'standesdb_images'"
                )
            ).scalars()
        )

    def index_definition(self, name: str) -> str:
        return self._db.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"), {"n": name}
        ).scalar_one()

    def constraint_names(self) -> set[str]:
        return set(
            self._db.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'standesdb_images'::regclass"
                )
            ).scalars()
        )


@pytest.fixture
def revision(db_session: Session) -> _Revision:
    return _Revision(db_session)


@pytest.fixture
def before(revision: _Revision) -> _Revision:
    """The schema as it was before the revision: the column is still `default`."""
    revision.downgrade()
    return revision


def _contact(db: Session, name: str) -> object:
    return db.execute(
        text(
            "INSERT INTO contacts (name, kontakttyp) VALUES (:name, 'person') "
            "RETURNING id"
        ),
        {"name": name},
    ).scalar_one()


def _image(db: Session, owner: object, hash_: str, column: str, value: bool) -> None:
    db.execute(
        text(
            f'INSERT INTO standesdb_images (sha256_hash, owner_contact_id, "{column}") '  # noqa: S608
            "VALUES (:hash, :owner, :value)"
        ),
        {"hash": hash_, "owner": owner, "value": value},
    )


class TestSchema:
    def test_the_column_is_called_default_before(self, before: _Revision) -> None:
        assert "default" in before.columns()
        assert "is_default" not in before.columns()

    def test_the_column_is_called_is_default_afterwards(
        self, before: _Revision
    ) -> None:
        before.upgrade()

        assert "is_default" in before.columns()
        assert "default" not in before.columns()

    def test_the_current_schema_matches_the_revision(self, revision: _Revision) -> None:
        assert "is_default" in revision.columns()
        assert "default" not in revision.columns()

    def test_the_not_null_constraint_follows_the_name(self, before: _Revision) -> None:
        assert "standesdb_images_default_not_null" in before.constraint_names()

        before.upgrade()

        names = before.constraint_names()
        assert "standesdb_images_is_default_not_null" in names
        assert "standesdb_images_default_not_null" not in names

    def test_the_downgrade_restores_the_old_names(self, before: _Revision) -> None:
        before.upgrade()

        before.downgrade()

        assert "default" in before.columns()
        assert "is_default" not in before.columns()
        assert "standesdb_images_default_not_null" in before.constraint_names()

    def test_the_default_indexes_follow_the_column(self, before: _Revision) -> None:
        before.upgrade()

        for name in DEFAULT_INDEXES:
            definition = before.index_definition(name)
            assert "is_default" in definition
            assert '"default"' not in definition

    def test_the_model_declares_the_new_name(self, revision: _Revision) -> None:
        predicates = [
            str(index.dialect_options["postgresql"]["where"])
            for index in StandesdbImage.__table__.indexes
            if index.name in DEFAULT_INDEXES
        ]

        assert "is_default" in StandesdbImage.__table__.columns
        assert "default" not in StandesdbImage.__table__.columns
        assert len(predicates) == len(DEFAULT_INDEXES)
        assert all(predicate.startswith("is_default AND") for predicate in predicates)
        for name in DEFAULT_INDEXES:
            assert "(is_default AND" in revision.index_definition(name)


class TestData:
    def test_the_flags_survive_the_rename(
        self, db_session: Session, before: _Revision
    ) -> None:
        owner = _contact(db_session, "A")
        _image(db_session, owner, "a" * 64, "default", True)
        _image(db_session, owner, "b" * 64, "default", False)

        before.upgrade()

        flags = db_session.execute(
            text(
                "SELECT sha256_hash, is_default FROM standesdb_images "
                "WHERE owner_contact_id = :owner"
            ),
            {"owner": owner},
        ).all()
        assert {(row.sha256_hash[0], row.is_default) for row in flags} == {
            ("a", True),
            ("b", False),
        }

    def test_the_flags_survive_the_downgrade(
        self, db_session: Session, revision: _Revision
    ) -> None:
        owner = _contact(db_session, "A")
        _image(db_session, owner, "a" * 64, "is_default", True)

        revision.downgrade()

        assert db_session.execute(
            text('SELECT "default" FROM standesdb_images WHERE owner_contact_id = :o'),
            {"o": owner},
        ).scalar_one()

    def test_a_second_default_image_is_still_refused(
        self, db_session: Session, before: _Revision
    ) -> None:
        before.upgrade()
        owner = _contact(db_session, "A")
        _image(db_session, owner, "a" * 64, "is_default", True)

        with pytest.raises(IntegrityError), db_session.begin_nested():
            _image(db_session, owner, "b" * 64, "is_default", True)

    def test_a_new_row_is_not_a_default_image(
        self, db_session: Session, revision: _Revision
    ) -> None:
        owner = _contact(db_session, "A")
        db_session.execute(
            text(
                "INSERT INTO standesdb_images (sha256_hash, owner_contact_id) "
                "VALUES (:hash, :owner)"
            ),
            {"hash": "c" * 64, "owner": owner},
        )

        assert not db_session.execute(
            text("SELECT is_default FROM standesdb_images WHERE sha256_hash = :h"),
            {"h": "c" * 64},
        ).scalar_one()


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
