"""Alembic revision c3e5a7f9b1d2 (public texts not blank and not oversized, gallery
types, named special contacts): it refuses data that would break a new rule,
changes nothing in that case, and can be undone.

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
    / "alembic/versions/c3e5a7f9b1d2_public_content_checks.py"
)

TEXT_CHECKS = {
    "public_site_about_tabs_title_check": ("public_site_about_tabs", "title"),
    "public_site_about_tabs_body_check": ("public_site_about_tabs", "body"),
    "public_site_programm_hints_text_check": ("public_site_programm_hints", "text"),
    "public_site_quotes_quote_check": ("public_site_quotes", "quote"),
    "public_site_quotes_author_check": ("public_site_quotes", "author"),
}
GALLERY_CHECKS = (
    "public_gallery_images_content_type_check",
    "public_gallery_images_extension_check",
)


class _Revision:
    def __init__(self, db: Session) -> None:
        self._db = db
        spec = importlib.util.spec_from_file_location(
            "public_content_checks_revision", REVISION_FILE
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

    def special_contact_name_is_required(self) -> bool:
        return (
            self._db.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'p4x_special_contacts' AND column_name = 'cn'"
                )
            ).scalar()
            == "NO"
        )

    def untouched(self) -> bool:
        """True while the schema is still the one before the revision."""
        return (
            all(
                self.check_definition(name) == f"CHECK ((char_length({column}) > 0))"
                for name, (_table, column) in TEXT_CHECKS.items()
            )
            and all(self.check_definition(name) is None for name in GALLERY_CHECKS)
            and not self.special_contact_name_is_required()
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


def _image(db: Session, **changes: object) -> object:
    columns = {
        "sha256_hash": "a" * 64,
        "extension": "jpg",
        "content_type": "image/jpeg",
        "size": 1,
        "width": 1,
        "height": 1,
        "sort_order": 1,
        "is_published": True,
    } | changes
    return _insert(db, "public_gallery_images", **columns)


def _set_about_tab(db: Session, column: str, value: str) -> object:
    return db.execute(
        text(
            f"UPDATE public_site_about_tabs SET {column} = :v "  # noqa: S608
            "WHERE slot = 'anfang' RETURNING id"
        ),
        {"v": value},
    ).scalar_one()


class TestUpgradeRefusesBadData:
    @pytest.mark.parametrize("column", ["title", "body"])
    def test_a_blank_about_tab(
        self, db_session: Session, before: _Revision, column: str
    ) -> None:
        row = _set_about_tab(db_session, column, " \t\n ")

        with pytest.raises(
            RuntimeError, match=rf"about_tabs\.{column} is blank"
        ) as exc:
            before.upgrade()

        assert str(row) in str(exc.value)

    @pytest.mark.parametrize(("column", "limit"), [("title", 100), ("body", 6000)])
    def test_an_oversized_about_tab(
        self, db_session: Session, before: _Revision, column: str, limit: int
    ) -> None:
        row = _set_about_tab(db_session, column, "x" * (limit + 1))

        with pytest.raises(RuntimeError, match=rf"about_tabs\.{column}") as exc:
            before.upgrade()

        assert str(row) in str(exc.value)

    def test_a_blank_hint(self, db_session: Session, before: _Revision) -> None:
        row = _insert(
            db_session, "public_site_programm_hints", text="   ", sort_order=1
        )

        with pytest.raises(RuntimeError, match=r"programm_hints\.text") as exc:
            before.upgrade()

        assert str(row) in str(exc.value)

    @pytest.mark.parametrize(
        ("quote", "author", "column"),
        [("   ", "A", "quote"), ("Q", "\t", "author"), ("q" * 501, "A", "quote")],
    )
    def test_a_bad_quote(
        self,
        db_session: Session,
        before: _Revision,
        quote: str,
        author: str,
        column: str,
    ) -> None:
        row = _insert(
            db_session, "public_site_quotes", quote=quote, author=author, sort_order=1
        )

        with pytest.raises(RuntimeError, match=rf"quotes\.{column}") as exc:
            before.upgrade()

        assert str(row) in str(exc.value)

    @pytest.mark.parametrize(
        "changes",
        [
            {"content_type": "image/gif"},
            {"extension": "svg"},
            {"content_type": "IMAGE/JPEG"},
        ],
    )
    def test_a_gallery_image_of_another_type(
        self, db_session: Session, before: _Revision, changes: dict[str, str]
    ) -> None:
        row = _image(db_session, **changes)

        with pytest.raises(RuntimeError, match="Gallery images") as exc:
            before.upgrade()

        assert str(row) in str(exc.value)

    def test_a_special_contact_without_a_name(
        self, db_session: Session, before: _Revision
    ) -> None:
        row = _insert(db_session, "p4x_special_contacts", cn=None)

        with pytest.raises(RuntimeError, match="Special contacts") as exc:
            before.upgrade()

        assert str(row) in str(exc.value)

    def test_the_longest_accepted_values_pass(
        self, db_session: Session, before: _Revision
    ) -> None:
        _set_about_tab(db_session, "body", "ä" * 6000)
        _insert(
            db_session,
            "public_site_quotes",
            quote="ä" * 500,
            author="ä" * 100,
            sort_order=1,
        )
        _image(db_session, extension="png", content_type="image/png")

        before.upgrade()

        assert before.special_contact_name_is_required()

    @pytest.mark.parametrize("bad", ["text", "image", "contact"])
    def test_nothing_is_changed_when_the_data_is_refused(
        self, db_session: Session, before: _Revision, bad: str
    ) -> None:
        if bad == "text":
            _insert(db_session, "public_site_programm_hints", text=" ", sort_order=1)
        if bad == "image":
            _image(db_session, content_type="image/gif")
        if bad == "contact":
            _insert(db_session, "p4x_special_contacts", cn=None)

        with pytest.raises(RuntimeError):
            before.upgrade()

        assert before.untouched()

    def test_the_upgrade_runs_once_the_data_is_corrected(
        self, db_session: Session, before: _Revision
    ) -> None:
        _insert(db_session, "p4x_special_contacts", cn=None)
        with pytest.raises(RuntimeError):
            before.upgrade()
        db_session.execute(text("UPDATE p4x_special_contacts SET cn = 'Name'"))

        before.upgrade()

        assert before.special_contact_name_is_required()


class TestSchema:
    def test_the_schema_before_had_none_of_the_rules(self, before: _Revision) -> None:
        assert before.untouched()

    def test_the_rules_exist_afterwards(self, before: _Revision) -> None:
        before.upgrade()

        for name, (_table, column) in TEXT_CHECKS.items():
            assert column in (before.check_definition(name) or "")
            assert "char_length" in (before.check_definition(name) or "")
            assert "<=" in (before.check_definition(name) or "")
        assert "image/jpeg" in (
            before.check_definition("public_gallery_images_content_type_check") or ""
        )
        assert "jpg" in (
            before.check_definition("public_gallery_images_extension_check") or ""
        )
        assert before.special_contact_name_is_required()

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
