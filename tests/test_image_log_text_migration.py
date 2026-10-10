"""Alembic revision e7a9c1b3d5f6 (one image and one default per owner, time indexes
on the log tables, white-space checks of the site texts): it refuses data that
would break a new rule, changes nothing in that case, and can be undone.

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
    / "alembic/versions/e7a9c1b3d5f6_image_uniqueness_log_indexes_text_checks.py"
)

NEW_IMAGE_INDEXES = (
    "standesdb_images_member_hash_active_uniq",
    "standesdb_images_contact_hash_active_uniq",
    "standesdb_images_member_default_uniq",
    "standesdb_images_contact_default_uniq",
)
OLD_IMAGE_INDEX = "standesdb_images_owner_hash_active_uniq"
NEW_LOG_INDEXES = (
    "ix_request_logs_created_at",
    "ix_request_logs_member_id_created_at",
    "ix_sent_emails_created_at",
)
OLD_LOG_INDEX = "ix_request_logs_member_id"
TEXT_CHECKS = {
    "public_site_settings_heading_check": "about_video_heading",
    "public_site_settings_gallery_heading_check": "gallery_heading",
    "public_site_social_links_label_check": "label",
}


class _Revision:
    def __init__(self, db: Session) -> None:
        self._db = db
        spec = importlib.util.spec_from_file_location(
            "image_log_text_revision", REVISION_FILE
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

    def has_index(self, name: str) -> bool:
        return bool(
            self._db.execute(
                text("SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": name}
            ).scalar()
        )

    def index_definition(self, name: str) -> str:
        return self._db.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"), {"n": name}
        ).scalar_one()

    def check_definition(self, name: str) -> str:
        return self._db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"
            ),
            {"n": name},
        ).scalar_one()

    def untouched(self) -> bool:
        """True while the schema is still the one before the revision."""
        return (
            self.has_index(OLD_IMAGE_INDEX)
            and "(sha256_hash, owner_member_id, owner_contact_id)"
            in self.index_definition(OLD_IMAGE_INDEX)
            and self.has_index(OLD_LOG_INDEX)
            and not any(self.has_index(name) for name in NEW_IMAGE_INDEXES)
            and not any(self.has_index(name) for name in NEW_LOG_INDEXES)
            and all(
                self.check_definition(name) == f"CHECK ((char_length({column}) > 0))"
                for name, column in TEXT_CHECKS.items()
            )
        )


@pytest.fixture
def revision(db_session: Session) -> _Revision:
    return _Revision(db_session)


@pytest.fixture
def before(revision: _Revision, db_session: Session) -> _Revision:
    """The schema as it was before the revision, with an organisation and a state.

    A later revision renamed standesdb_images.default to is_default; this
    revision's statements still name the column default, so the old name is
    put back inside the test transaction."""
    revision.downgrade()
    db_session.execute(
        text('ALTER TABLE standesdb_images RENAME COLUMN is_default TO "default"')
    )
    db_session.add_all(
        [
            Org(id="vbw", label="V", sort_order=1),
            State(id="up", label="U", sort_order=1),
        ]
    )
    db_session.flush()
    return revision


def _insert(db: Session, table: str, **columns: object) -> object:
    names = ", ".join(f'"{key}"' for key in columns)
    values = ", ".join(f":{key}" for key in columns)
    return db.execute(
        text(f"INSERT INTO {table} ({names}) VALUES ({values}) RETURNING id"),  # noqa: S608
        columns,
    ).scalar_one()


def _member(db: Session, name: str) -> object:
    return _insert(
        db, "members", vorname=name, nachname="X", org_id="vbw", state_id="up"
    )


def _contact(db: Session, name: str) -> object:
    return _insert(db, "contacts", name=name, kontakttyp="person")


def _image(db: Session, hash_: str, **columns: object) -> object:
    return _insert(db, "standesdb_images", sha256_hash=hash_, **columns)


class TestUpgradeRefusesBadData:
    @pytest.mark.parametrize("owner", ["member", "contact"])
    def test_an_owner_holding_the_same_image_twice(
        self, db_session: Session, before: _Revision, owner: str
    ) -> None:
        owner_id = (_member if owner == "member" else _contact)(db_session, "A")
        column = f"owner_{owner}_id"
        _image(db_session, "a" * 64, **{column: owner_id})
        _image(db_session, "a" * 64, **{column: owner_id})

        with pytest.raises(RuntimeError, match=f"Owners \\({owner}\\) holding") as exc:
            before.upgrade()

        assert str(owner_id) in str(exc.value)

    @pytest.mark.parametrize("owner", ["member", "contact"])
    def test_an_owner_with_two_default_images(
        self, db_session: Session, before: _Revision, owner: str
    ) -> None:
        owner_id = (_member if owner == "member" else _contact)(db_session, "A")
        column = f"owner_{owner}_id"
        _image(db_session, "a" * 64, **{column: owner_id}, default=True)
        _image(db_session, "b" * 64, **{column: owner_id}, default=True)

        with pytest.raises(
            RuntimeError, match=f"Owners \\({owner}\\) with more"
        ) as exc:
            before.upgrade()

        assert str(owner_id) in str(exc.value)

    def test_deleted_images_and_other_owners_do_not_clash(
        self, db_session: Session, before: _Revision
    ) -> None:
        first = _member(db_session, "A")
        second = _member(db_session, "B")
        _image(db_session, "a" * 64, owner_member_id=first, default=True)
        _image(
            db_session,
            "a" * 64,
            owner_member_id=first,
            default=True,
            deleted_at="2026-01-01",
        )
        _image(db_session, "a" * 64, owner_member_id=second, default=True)

        before.upgrade()

        assert before.has_index("standesdb_images_member_default_uniq")

    @pytest.mark.parametrize(
        ("column", "table"),
        [
            ("about_video_heading", "public_site_settings"),
            ("gallery_heading", "public_site_settings"),
        ],
    )
    def test_a_blank_site_heading(
        self, db_session: Session, before: _Revision, column: str, table: str
    ) -> None:
        row = db_session.execute(
            text(f"UPDATE {table} SET {column} = ' \t' RETURNING id")  # noqa: S608
        ).scalar_one()

        with pytest.raises(RuntimeError, match=f"{table}\\.{column}") as exc:
            before.upgrade()

        assert str(row) in str(exc.value)

    def test_a_blank_social_link_label(
        self, db_session: Session, before: _Revision
    ) -> None:
        row = _insert(
            db_session,
            "public_site_social_links",
            platform="instagram",
            label="  ",
            url="https://example.at",
            sort_order=1,
        )

        with pytest.raises(RuntimeError, match=r"social_links\.label") as exc:
            before.upgrade()

        assert str(row) in str(exc.value)

    @pytest.mark.parametrize("bad", ["duplicate", "default", "heading"])
    def test_nothing_is_changed_when_the_data_is_refused(
        self, db_session: Session, before: _Revision, bad: str
    ) -> None:
        owner = _member(db_session, "A")
        if bad == "duplicate":
            _image(db_session, "a" * 64, owner_member_id=owner)
            _image(db_session, "a" * 64, owner_member_id=owner)
        if bad == "default":
            _image(db_session, "a" * 64, owner_member_id=owner, default=True)
            _image(db_session, "b" * 64, owner_member_id=owner, default=True)
        if bad == "heading":
            db_session.execute(
                text("UPDATE public_site_settings SET gallery_heading = ' '")
            )

        with pytest.raises(RuntimeError):
            before.upgrade()

        assert before.untouched()

    def test_the_upgrade_runs_once_the_data_is_corrected(
        self, db_session: Session, before: _Revision
    ) -> None:
        owner = _member(db_session, "A")
        _image(db_session, "a" * 64, owner_member_id=owner)
        extra = _image(db_session, "a" * 64, owner_member_id=owner)
        with pytest.raises(RuntimeError):
            before.upgrade()
        db_session.execute(
            text("UPDATE standesdb_images SET deleted_at = now() WHERE id = :id"),
            {"id": extra},
        )

        before.upgrade()

        assert before.has_index("standesdb_images_member_hash_active_uniq")


class TestSchema:
    def test_the_schema_before_had_none_of_the_rules(self, before: _Revision) -> None:
        assert before.untouched()

    def test_the_rules_exist_afterwards(self, before: _Revision) -> None:
        before.upgrade()

        assert not before.has_index(OLD_IMAGE_INDEX)
        assert not before.has_index(OLD_LOG_INDEX)
        assert all(before.has_index(name) for name in NEW_IMAGE_INDEXES)
        assert all(before.has_index(name) for name in NEW_LOG_INDEXES)
        for name, column in TEXT_CHECKS.items():
            assert before.check_definition(name).startswith(f"CHECK (({column} ~ ")

    def test_the_image_indexes_are_unique_and_partial(self, before: _Revision) -> None:
        before.upgrade()

        for name in NEW_IMAGE_INDEXES:
            definition = before.index_definition(name)
            assert "UNIQUE" in definition
            assert "deleted_at IS NULL" in definition
        assert '"default"' in before.index_definition(
            "standesdb_images_member_default_uniq"
        )

    def test_the_combined_log_index_leads_with_the_member(
        self, before: _Revision
    ) -> None:
        before.upgrade()

        assert "(member_id, created_at)" in before.index_definition(
            "ix_request_logs_member_id_created_at"
        )

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
