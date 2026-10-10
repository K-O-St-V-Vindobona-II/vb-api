"""Public texts are never blank or oversized, the gallery only holds the accepted
image types and a special contact always has a name."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.enums import AboutTabSlot
from app.models.p4x_specialcontact import P4xSpecialcontact
from app.models.public_gallery_image import PublicGalleryImage
from app.models.public_site_about_tab import PublicSiteAboutTab
from app.models.public_site_programm_hint import PublicSiteProgrammHint
from app.models.public_site_quote import PublicSiteQuote
from app.schemas.public_content import (
    AboutTabUpdateRequest,
    ProgrammHintRequest,
    QuoteRequest,
)

BLANKS = ["   ", "\t \n", "\r\n"]


def _api_limit(schema: type, field: str) -> int:
    for item in schema.model_fields[field].metadata:
        if hasattr(item, "max_length"):
            return item.max_length
    msg = f"{schema.__name__}.{field} has no maximum length"
    raise AssertionError(msg)


def _about_tab(db, **changes):
    """The three about tabs are seeded by the migrations: change the first one."""
    tab = db.query(PublicSiteAboutTab).filter_by(slot=AboutTabSlot.ANFANG).one()
    for column, value in changes.items():
        setattr(tab, column, value)
    return tab


_LIMITED_FIELDS = pytest.mark.parametrize(
    ("schema", "field", "build"),
    [
        (
            AboutTabUpdateRequest,
            "title",
            lambda db, text: _about_tab(db, title=text),
        ),
        (
            AboutTabUpdateRequest,
            "body",
            lambda db, text: _about_tab(db, body=text),
        ),
        (
            ProgrammHintRequest,
            "text",
            lambda _db, text: PublicSiteProgrammHint(content=text, sort_order=1),
        ),
        (
            QuoteRequest,
            "quote",
            lambda _db, text: PublicSiteQuote(quote=text, author="x", sort_order=1),
        ),
        (
            QuoteRequest,
            "author",
            lambda _db, text: PublicSiteQuote(quote="x", author=text, sort_order=1),
        ),
    ],
    ids=["title", "body", "hint", "quote", "author"],
)


def _commit_is_refused_by_a_check(db) -> None:
    with pytest.raises(IntegrityError, match="check constraint"):
        db.commit()


class TestBlankTexts:
    @pytest.mark.parametrize("blank", BLANKS)
    def test_quote_of_blanks_is_refused(self, db_session, blank):
        db_session.add(PublicSiteQuote(quote=blank, author="Autor", sort_order=1))

        _commit_is_refused_by_a_check(db_session)

    @pytest.mark.parametrize("blank", BLANKS)
    def test_author_of_blanks_is_refused(self, db_session, blank):
        db_session.add(PublicSiteQuote(quote="Text", author=blank, sort_order=1))

        _commit_is_refused_by_a_check(db_session)

    @pytest.mark.parametrize("blank", BLANKS)
    def test_hint_of_blanks_is_refused(self, db_session, blank):
        db_session.add(PublicSiteProgrammHint(content=blank, sort_order=1))

        _commit_is_refused_by_a_check(db_session)

    @pytest.mark.parametrize("column", ["title", "body"])
    @pytest.mark.parametrize("blank", BLANKS)
    def test_about_tab_of_blanks_is_refused(self, db_session, column, blank):
        _about_tab(db_session, **{column: blank})

        _commit_is_refused_by_a_check(db_session)

    def test_empty_text_is_still_refused(self, db_session):
        db_session.add(PublicSiteQuote(quote="", author="Autor", sort_order=1))

        _commit_is_refused_by_a_check(db_session)

    def test_normal_text_is_accepted(self, db_session):
        db_session.add(
            PublicSiteQuote(quote="Ein Zitat", author="Jemand", sort_order=1)
        )

        db_session.commit()

    def test_text_with_blanks_around_it_is_accepted(self, db_session):
        db_session.add(PublicSiteProgrammHint(content="  Mit Rand \n", sort_order=1))

        db_session.commit()


class TestLengthLimits:
    """The database holds the same maximum lengths as the API."""

    @_LIMITED_FIELDS
    def test_the_longest_api_value_fits(self, db_session, schema, field, build):
        db_session.add(build(db_session, "ä" * _api_limit(schema, field)))

        db_session.commit()

    @_LIMITED_FIELDS
    def test_one_character_more_is_refused(self, db_session, schema, field, build):
        db_session.add(build(db_session, "ä" * (_api_limit(schema, field) + 1)))

        _commit_is_refused_by_a_check(db_session)


class TestGalleryTypes:
    def _image(self, **changes):
        values = {
            "sha256_hash": "a" * 64,
            "extension": "jpg",
            "content_type": "image/jpeg",
            "size": 10,
            "width": 1,
            "height": 1,
            "sort_order": 1,
        }
        return PublicGalleryImage(**{**values, **changes})

    def test_jpeg_and_png_are_accepted(self, db_session):
        db_session.add_all(
            [
                self._image(),
                self._image(
                    sha256_hash="b" * 64,
                    extension="png",
                    content_type="image/png",
                    sort_order=2,
                ),
            ]
        )

        db_session.commit()

    @pytest.mark.parametrize(
        "changes",
        [
            {"content_type": "image/gif"},
            {"content_type": "text/html"},
            {"content_type": "IMAGE/JPEG"},
            {"extension": "svg"},
            {"extension": "JPG"},
        ],
    )
    def test_other_types_are_refused(self, db_session, changes):
        db_session.add(self._image(**changes))

        _commit_is_refused_by_a_check(db_session)


class TestSpecialContactName:
    def test_a_special_contact_without_a_name_is_refused(self, db_session):
        db_session.add(P4xSpecialcontact(cn=None))

        with pytest.raises(IntegrityError, match="not-null constraint"):
            db_session.commit()

    def test_a_special_contact_with_a_name_is_accepted(self, db_session):
        db_session.add(P4xSpecialcontact(cn="Konto-Intern"))

        db_session.commit()


class TestModelsDeclareTheChecks:
    """Alembic does not compare CHECK constraints, so the models are checked here."""

    @staticmethod
    def _checks(model) -> dict[str, str]:
        return {
            c.name: str(c.sqltext)
            for c in model.__table__.constraints
            if c.__class__.__name__ == "CheckConstraint"
        }

    @pytest.mark.parametrize(
        ("model", "name", "column", "limit"),
        [
            (PublicSiteAboutTab, "public_site_about_tabs_title_check", "title", 100),
            (PublicSiteAboutTab, "public_site_about_tabs_body_check", "body", 6000),
            (
                PublicSiteProgrammHint,
                "public_site_programm_hints_text_check",
                "text",
                300,
            ),
            (PublicSiteQuote, "public_site_quotes_quote_check", "quote", 500),
            (PublicSiteQuote, "public_site_quotes_author_check", "author", 100),
        ],
    )
    def test_text_checks(self, model, name, column, limit):
        assert self._checks(model)[name] == (
            f"{column} ~ '[^[:space:]]' AND char_length({column}) <= {limit}"
        )

    def test_gallery_checks(self):
        checks = self._checks(PublicGalleryImage)

        assert checks["public_gallery_images_content_type_check"] == (
            "content_type IN ('image/jpeg', 'image/png')"
        )
        assert checks["public_gallery_images_extension_check"] == (
            "extension IN ('jpg', 'png')"
        )

    def test_special_contact_name_is_required(self):
        assert P4xSpecialcontact.__table__.c.cn.nullable is False
