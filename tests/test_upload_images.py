"""Uploaded images are decoded only as JPEG or PNG, and their type comes from
their bytes: the Content-Type header is chosen by the client, so it cannot
decide which of Pillow's many decoders runs on a file that any member sends."""

import io

import pytest
from PIL import Image as PILImage

from app.core.upload_images import inspect_upload_image
from app.models.public_gallery_image import PublicGalleryImage
from app.models.standesdb_image import StandesdbImage
from tests.public_site import test_gallery as gallery
from tests.standesdb import test_image_gallery as members


def _image(
    image_format: str, mode: str = "RGB", size: tuple[int, int] = (20, 20)
) -> bytes:
    buffer = io.BytesIO()
    PILImage.new(mode, size, color="red" if mode == "RGB" else 0).save(
        buffer, format=image_format
    )
    return buffer.getvalue()


def _jpeg() -> bytes:
    return _image("JPEG")


def _png() -> bytes:
    return _image("PNG")


def _gif() -> bytes:
    return _image("GIF", mode="P")


def _too_many_pixels() -> bytes:
    # A bit per pixel, so 48 KB on the wire, but far beyond Pillow's limit.
    return _image("PNG", mode="1", size=(20000, 20000))


class TestInspectUploadImage:
    def test_a_jpeg_is_recognised(self):
        result = inspect_upload_image(_jpeg())

        assert (result.content_type, result.width, result.height) == (
            "image/jpeg",
            20,
            20,
        )

    def test_a_png_is_recognised(self):
        assert inspect_upload_image(_png()).content_type == "image/png"

    @pytest.mark.parametrize(
        "payload", [b"", b"not an image", b"%!PS-Adobe-3.0 EPSF-3.0\n"]
    )
    def test_anything_else_is_rejected(self, payload):
        with pytest.raises(OSError, match="cannot identify image file"):
            inspect_upload_image(payload)

    def test_a_valid_image_of_another_format_is_rejected(self):
        with pytest.raises(OSError, match="cannot identify image file"):
            inspect_upload_image(_gif())

    def test_a_file_beyond_the_pixel_limit_is_rejected_as_a_value_error(self):
        with pytest.raises(ValueError, match="too many pixels"):
            inspect_upload_image(_too_many_pixels())


class TestMemberImageUpload:
    @pytest.fixture
    def headers(self, client, db_session):
        members._seed(db_session)
        member = members._self_member(db_session)
        return {"member": member, **members._headers(client, db_session, member)}

    def _upload(self, client, headers, content: bytes, declared: str):
        auth = {"Authorization": headers["Authorization"]}
        return client.post(
            "/api/standesdb/members/me/images",
            headers=auth,
            files={"file": ("avatar.jpg", io.BytesIO(content), declared)},
        )

    def test_another_format_is_refused_whatever_the_header_says(
        self, client, db_session, headers
    ):
        response = self._upload(client, headers, _gif(), "image/jpeg")

        assert response.status_code == 422
        assert response.json()["detail"] == "Datei ist kein gültiges Bild."
        assert db_session.query(StandesdbImage).count() == 0

    def test_a_file_beyond_the_pixel_limit_is_refused_not_a_server_error(
        self, client, db_session, headers
    ):
        response = self._upload(client, headers, _too_many_pixels(), "image/png")

        assert response.status_code == 422
        assert db_session.query(StandesdbImage).count() == 0

    def test_the_stored_type_is_the_real_one(self, client, db_session, headers):
        response = self._upload(client, headers, _png(), "image/jpeg")

        assert response.status_code == 201
        stored = db_session.query(StandesdbImage).one()
        assert stored.type == "image/png"

    def test_a_jpeg_is_accepted(self, client, db_session, headers):
        response = self._upload(client, headers, _jpeg(), "image/jpeg")

        assert response.status_code == 201
        assert db_session.query(StandesdbImage).one().type == "image/jpeg"

    def test_the_declared_type_still_has_to_be_an_allowed_one(
        self, client, db_session, headers
    ):
        response = self._upload(client, headers, _jpeg(), "image/gif")

        assert response.status_code == 422
        assert response.json()["detail"] == "Nur JPEG- und PNG-Dateien erlaubt."


class TestGalleryUpload:
    @pytest.fixture
    def auth(self, db_session):
        gallery._seed(db_session)
        return gallery._headers(db_session, gallery._admin(db_session))

    def _upload(self, client, auth, content: bytes, declared: str):
        return client.post(
            "/api/public-gallery-admin/images",
            headers=auth,
            files={"file": ("a.jpg", io.BytesIO(content), declared)},
        )

    def test_another_format_is_refused_whatever_the_header_says(
        self, client, db_session, auth
    ):
        response = self._upload(client, auth, _gif(), "image/jpeg")

        assert response.status_code == 422
        assert db_session.query(PublicGalleryImage).count() == 0

    def test_a_file_beyond_the_pixel_limit_is_refused_not_a_server_error(
        self, client, db_session, auth
    ):
        response = self._upload(client, auth, _too_many_pixels(), "image/png")

        assert response.status_code == 422
        assert db_session.query(PublicGalleryImage).count() == 0

    def test_the_stored_type_is_the_real_one(self, client, db_session, auth):
        response = self._upload(client, auth, _png(), "image/jpeg")

        assert response.status_code == 201
        assert db_session.query(PublicGalleryImage).one().content_type == "image/png"
