"""Tests for S3 storage client and thumbnail generation."""

import io
from unittest.mock import patch

import pytest
from PIL import Image as PILImage

from app.core import storage as storage_module
from app.core.storage import (
    generate_thumbnail,
)


def _make_jpeg(width: int = 200, height: int = 100) -> bytes:
    img = PILImage.new("RGB", (width, height), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_png_rgba(width: int = 200, height: int = 100) -> bytes:
    img = PILImage.new("RGBA", (width, height), color=(0, 0, 255, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_jpeg_with_orientation(width: int, height: int, orientation: int) -> bytes:
    """Build a JPEG whose raw pixels are (width, height) but whose EXIF
    Orientation tag (0x0112) declares a rotation/flip, mimicking a phone photo.
    """
    img = PILImage.new("RGB", (width, height), color="red")
    exif = PILImage.Exif()
    exif[0x0112] = orientation
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


class TestGenerateThumbnail:
    def test_jpeg_resize_landscape(self):
        data = _make_jpeg(800, 400)
        thumb, ct = generate_thumbnail(data, 200)
        assert ct == "image/jpeg"
        img = PILImage.open(io.BytesIO(thumb))
        assert img.width == 200
        assert img.height == 100

    def test_jpeg_resize_portrait(self):
        data = _make_jpeg(100, 400)
        thumb, ct = generate_thumbnail(data, 200)
        assert ct == "image/jpeg"
        img = PILImage.open(io.BytesIO(thumb))
        assert img.height == 200
        assert img.width == 50

    def test_png_converts_to_jpeg_by_default(self):
        data = _make_png_rgba(200, 200)
        thumb, ct = generate_thumbnail(data, 100)
        assert ct == "image/jpeg"
        img = PILImage.open(io.BytesIO(thumb))
        assert img.mode == "RGB"

    def test_png_preserved_when_requested(self):
        data = _make_png_rgba(200, 200)
        _thumb, ct = generate_thumbnail(
            data,
            100,
            preserve_png=True,
            source_mime="image/png",
        )
        assert ct == "image/png"

    def test_corrupt_data_raises(self):
        with pytest.raises((OSError, ValueError)):
            generate_thumbnail(b"not-an-image", 100)

    def test_minimum_dimension(self):
        data = _make_jpeg(2, 2)
        thumb, _ = generate_thumbnail(data, 1)
        img = PILImage.open(io.BytesIO(thumb))
        assert img.width >= 1
        assert img.height >= 1

    def test_exif_orientation_6_rotates_to_portrait(self):
        # Raw pixels are landscape (400x300), but Orientation 6 (90° CW)
        # marks this as a portrait phone photo taken sideways.
        data = _make_jpeg_with_orientation(400, 300, 6)
        thumb, _ct = generate_thumbnail(data, 200)
        img = PILImage.open(io.BytesIO(thumb))
        assert img.height > img.width
        assert img.height == 200
        assert img.width == 150

    def test_exif_orientation_3_keeps_aspect(self):
        # Orientation 3 (180°) doesn't swap width/height, just confirms
        # exif_transpose() runs without error on a non-90°-rotation tag.
        data = _make_jpeg_with_orientation(400, 300, 3)
        thumb, _ct = generate_thumbnail(data, 200)
        img = PILImage.open(io.BytesIO(thumb))
        assert img.width == 200
        assert img.height == 150

    def test_no_exif_orientation_unaffected(self):
        # Sanity check: images without an Orientation tag (the existing
        # test fixtures) must resize exactly as before.
        data = _make_jpeg(800, 400)
        thumb, _ct = generate_thumbnail(data, 200)
        img = PILImage.open(io.BytesIO(thumb))
        assert img.width == 200
        assert img.height == 100


class TestStorageClient:
    def test_upload_download_roundtrip(self, mock_s3):
        mock_s3.upload("test/key", b"hello", "text/plain")
        result = mock_s3.download("test/key")
        assert result == b"hello"

    def test_download_with_metadata_preserves_content_type(self, mock_s3):
        mock_s3.upload("test/meta", b"hello", "image/png")
        data, content_type = mock_s3.download_with_metadata("test/meta")
        assert data == b"hello"
        assert content_type == "image/png"

    def test_exists_true(self, mock_s3):
        mock_s3.upload("test/exists", b"data")
        assert mock_s3.exists("test/exists") is True

    def test_exists_false(self, mock_s3):
        assert mock_s3.exists("test/nope") is False

    def test_delete(self, mock_s3):
        mock_s3.upload("test/del", b"data")
        mock_s3.delete("test/del")
        assert mock_s3.exists("test/del") is False

    def test_presigned_url_returns_string(self, mock_s3):
        mock_s3.upload("test/url", b"data")
        url = mock_s3.generate_presigned_url("test/url")
        assert isinstance(url, str)
        assert "test/url" in url

    def test_presigned_url_with_filename(self, mock_s3):
        mock_s3.upload("test/fn", b"data")
        url = mock_s3.generate_presigned_url(
            "test/fn",
            filename="doc.pdf",
        )
        assert "doc.pdf" in url

    def test_presigned_expiry_clamped_min(self, mock_s3):
        mock_s3.upload("test/exp", b"data")
        url = mock_s3.generate_presigned_url(
            "test/exp",
            expires_in=1,
        )
        assert isinstance(url, str)

    def test_presigned_expiry_clamped_max(self, mock_s3):
        mock_s3.upload("test/exp2", b"data")
        url = mock_s3.generate_presigned_url(
            "test/exp2",
            expires_in=999999,
        )
        assert isinstance(url, str)

    def test_upload_error_raises_runtime(self, mock_s3):
        from unittest.mock import patch

        from botocore.exceptions import ClientError

        with (
            patch.object(
                mock_s3._client,
                "put_object",
                side_effect=ClientError(
                    {"Error": {"Code": "500", "Message": "fail"}},
                    "PutObject",
                ),
            ),
            pytest.raises(RuntimeError, match="S3 upload failed"),
        ):
            mock_s3.upload("k", b"d")


class TestGetStorageSingleton:
    """Regression tests for the S3_ENDPOINT_URL default.

    A hardcoded "http://localhost:9000" fallback here would silently make
    production (where S3_ENDPOINT_URL is intentionally left unset to use
    real AWS S3) try to talk to a local MinIO instance instead.
    """

    def test_defaults_to_none_endpoint_when_unset(self, monkeypatch):
        # Also clear S3_PUBLIC_ENDPOINT_URL: if it were set, __init__ would
        # make a *second* boto3.client() call for the public client, and
        # call_args (last call) would then reflect that one instead.
        monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
        monkeypatch.delenv("S3_PUBLIC_ENDPOINT_URL", raising=False)
        old_singleton = storage_module._storage
        storage_module._storage = None
        try:
            with patch.object(storage_module.boto3, "client") as mock_client:
                storage_module._get_storage_singleton()
                assert mock_client.call_args.kwargs["endpoint_url"] is None
        finally:
            storage_module._storage = old_singleton

    def test_uses_explicit_endpoint_when_set(self, monkeypatch):
        monkeypatch.setenv("S3_ENDPOINT_URL", "https://minio.dev.example.com")
        monkeypatch.delenv("S3_PUBLIC_ENDPOINT_URL", raising=False)
        old_singleton = storage_module._storage
        storage_module._storage = None
        try:
            with patch.object(storage_module.boto3, "client") as mock_client:
                storage_module._get_storage_singleton()
                assert (
                    mock_client.call_args.kwargs["endpoint_url"]
                    == "https://minio.dev.example.com"
                )
        finally:
            storage_module._storage = old_singleton
