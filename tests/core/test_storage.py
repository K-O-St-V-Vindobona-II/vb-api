"""Tests for S3 storage client and thumbnail generation."""

import io

import pytest
from PIL import Image as PILImage

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


class TestStorageClient:
    def test_upload_download_roundtrip(self, mock_s3):
        mock_s3.upload("test/key", b"hello", "text/plain")
        result = mock_s3.download("test/key")
        assert result == b"hello"

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
