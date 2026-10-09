"""Pure unit tests for the request models of the public site."""

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas import public_content
from app.schemas.base import StrictInputModel, TrimmedInputModel
from app.schemas.public_content import (
    AboutTabUpdateRequest,
    ProgrammHintRequest,
    QuoteRequest,
    SiteSettingsUpdateRequest,
    SocialLinkCreateRequest,
    SocialLinkUpdateRequest,
)
from app.schemas.public_gallery import (
    MAX_CAPTION_LENGTH,
    GalleryImageUpdateRequest,
)

_BLANKS = ["", " ", "   ", "\t", " \n "]


class TestBlankValuesAreRejected:
    @pytest.mark.parametrize("blank", _BLANKS)
    def test_programm_hint(self, blank: str) -> None:
        with pytest.raises(ValidationError):
            ProgrammHintRequest(text=blank)

    @pytest.mark.parametrize("blank", _BLANKS)
    def test_quote_author(self, blank: str) -> None:
        with pytest.raises(ValidationError):
            QuoteRequest(quote="x", author=blank)

    @pytest.mark.parametrize("blank", _BLANKS)
    def test_quote_text(self, blank: str) -> None:
        with pytest.raises(ValidationError):
            QuoteRequest(quote=blank, author="a")

    @pytest.mark.parametrize("blank", _BLANKS)
    def test_about_tab(self, blank: str) -> None:
        with pytest.raises(ValidationError):
            AboutTabUpdateRequest(title=blank, body="text")
        with pytest.raises(ValidationError):
            AboutTabUpdateRequest(title="Titel", body=blank)

    @pytest.mark.parametrize("blank", _BLANKS)
    def test_social_link_update(self, blank: str) -> None:
        with pytest.raises(ValidationError):
            SocialLinkUpdateRequest(label=blank, url="https://example.at")

    @pytest.mark.parametrize("blank", _BLANKS)
    def test_social_link_create(self, blank: str) -> None:
        with pytest.raises(ValidationError):
            SocialLinkCreateRequest(
                platform="insta", label=blank, url="https://example.at"
            )

    @pytest.mark.parametrize("blank", _BLANKS)
    def test_site_settings(self, blank: str) -> None:
        valid = {
            "about_video_heading": "Video",
            "youtube_url": "dQw4w9WgXcQ",
            "calendar_id": "abc@group.calendar.google.com",
            "gallery_heading": "Galerie",
        }
        for field in ("about_video_heading", "gallery_heading"):
            with pytest.raises(ValidationError):
                SiteSettingsUpdateRequest(**{**valid, field: blank})


class TestValuesAreTrimmed:
    def test_programm_hint(self) -> None:
        assert ProgrammHintRequest(text="  Hi  ").text == "Hi"

    def test_quote(self) -> None:
        req = QuoteRequest(quote=" Numquam retro\n", author="\tJemand ")
        assert (req.quote, req.author) == ("Numquam retro", "Jemand")

    def test_about_tab(self) -> None:
        req = AboutTabUpdateRequest(title=" Titel ", body="\nText\n")
        assert (req.title, req.body) == ("Titel", "Text")

    def test_social_link(self) -> None:
        req = SocialLinkUpdateRequest(label=" Insta ", url=" https://example.at ")
        assert (req.label, req.url) == ("Insta", "https://example.at")

    def test_trimming_applies_before_the_length_limit(self) -> None:
        assert len(ProgrammHintRequest(text=" " + "x" * 300 + " ").text) == 300
        with pytest.raises(ValidationError):
            ProgrammHintRequest(text="x" * 301)


class TestRequestModelBase:
    def test_every_admin_request_trims(self) -> None:
        request_models = [
            cls
            for name, cls in vars(public_content).items()
            if name.endswith("Request")
            and isinstance(cls, type)
            and issubclass(cls, BaseModel)
        ]
        assert len(request_models) == 6
        assert all(issubclass(cls, TrimmedInputModel) for cls in request_models)
        assert all(issubclass(cls, StrictInputModel) for cls in request_models)


class TestGalleryImageUpdateRequest:
    @pytest.mark.parametrize("value", [5, ["x"], {"a": 1}, True])
    def test_caption_of_the_wrong_type_is_a_validation_error(
        self, value: object
    ) -> None:
        with pytest.raises(ValidationError):
            GalleryImageUpdateRequest(caption=value)

    def test_caption_limit(self) -> None:
        ok = GalleryImageUpdateRequest(caption="x" * MAX_CAPTION_LENGTH)
        assert ok.caption is not None
        assert len(ok.caption) == MAX_CAPTION_LENGTH
        with pytest.raises(ValidationError, match="Maximal"):
            GalleryImageUpdateRequest(caption="x" * (MAX_CAPTION_LENGTH + 1))

    def test_caption_may_be_empty_or_missing(self) -> None:
        assert GalleryImageUpdateRequest().caption is None
        assert GalleryImageUpdateRequest(caption="").caption == ""
