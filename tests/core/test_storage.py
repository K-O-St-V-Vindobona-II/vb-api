"""Tests for S3 storage client and thumbnail generation."""

import io
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError
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


class TestEnsureBucketExists:
    def test_noop_when_bucket_already_exists(self, mock_s3):
        with patch.object(mock_s3._client, "create_bucket") as mock_create:
            mock_s3.ensure_bucket_exists()
        mock_create.assert_not_called()

    def test_creates_bucket_when_missing(self):
        storage = storage_module.StorageClient(
            endpoint_url="https://s3.amazonaws.com",
            access_key="testing",
            secret_key="testing",
            bucket="brand-new-test-bucket",
        )
        with pytest.raises(ClientError):
            storage._client.head_bucket(Bucket="brand-new-test-bucket")

        storage.ensure_bucket_exists()

        # No longer raises - moto's in-memory S3 now has the bucket.
        storage._client.head_bucket(Bucket="brand-new-test-bucket")

    def test_omits_location_constraint_for_us_east_1(self):
        storage = storage_module.StorageClient(
            endpoint_url="https://s3.amazonaws.com",
            access_key="testing",
            secret_key="testing",
            bucket="us-east-bucket",
            region="us-east-1",
        )
        with (
            patch.object(
                storage._client,
                "head_bucket",
                side_effect=ClientError(
                    {"Error": {"Code": "404", "Message": "Not Found"}},
                    "HeadBucket",
                ),
            ),
            patch.object(storage._client, "create_bucket") as mock_create,
        ):
            storage.ensure_bucket_exists()

        mock_create.assert_called_once_with(Bucket="us-east-bucket")

    def test_includes_location_constraint_for_non_default_region(self):
        # AWS S3 rejects create_bucket() without a matching
        # CreateBucketConfiguration for any region other than us-east-1.
        storage = storage_module.StorageClient(
            endpoint_url="https://s3.amazonaws.com",
            access_key="testing",
            secret_key="testing",
            bucket="region-test-bucket",
            region="eu-central-1",
        )
        with (
            patch.object(
                storage._client,
                "head_bucket",
                side_effect=ClientError(
                    {"Error": {"Code": "404", "Message": "Not Found"}},
                    "HeadBucket",
                ),
            ),
            patch.object(storage._client, "create_bucket") as mock_create,
        ):
            storage.ensure_bucket_exists()

        mock_create.assert_called_once_with(
            Bucket="region-test-bucket",
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )


class TestGetStorageSingleton:
    """Regression tests for the S3_ENDPOINT_URL default.

    A hardcoded "http://localhost:9000" fallback here would silently make
    production (where S3_ENDPOINT_URL is intentionally left unset to use
    real AWS S3) try to talk to a local S3-compatible instance instead.
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
        monkeypatch.setenv("S3_ENDPOINT_URL", "https://storage.dev.example.com")
        monkeypatch.delenv("S3_PUBLIC_ENDPOINT_URL", raising=False)
        old_singleton = storage_module._storage
        storage_module._storage = None
        try:
            with patch.object(storage_module.boto3, "client") as mock_client:
                storage_module._get_storage_singleton()
                assert (
                    mock_client.call_args.kwargs["endpoint_url"]
                    == "https://storage.dev.example.com"
                )
        finally:
            storage_module._storage = old_singleton

    def test_raises_clear_error_when_access_key_missing(self, monkeypatch):
        # Regression: s3_access_key/s3_secret_key are Tier 2 (checked via
        # require_setting() at first use), not Tier 3 with an empty-string
        # default — an empty string would silently reach boto3 and only
        # fail deep inside botocore on the first real S3 call.
        monkeypatch.delenv("S3_ACCESS_KEY", raising=False)
        old_singleton = storage_module._storage
        storage_module._storage = None
        try:
            with pytest.raises(RuntimeError, match="S3_ACCESS_KEY is not set"):
                storage_module._get_storage_singleton()
        finally:
            storage_module._storage = old_singleton

    def test_raises_clear_error_when_secret_key_missing(self, monkeypatch):
        monkeypatch.delenv("S3_SECRET_KEY", raising=False)
        old_singleton = storage_module._storage
        storage_module._storage = None
        try:
            with pytest.raises(RuntimeError, match="S3_SECRET_KEY is not set"):
                storage_module._get_storage_singleton()
        finally:
            storage_module._storage = old_singleton

    def test_ensures_bucket_outside_production(self, monkeypatch):
        # Regression: a freshly provisioned non-prod stage's own storage
        # (e.g. vb-deploy's per-stage Garage) starts out with no bucket at
        # all - the singleton must self-heal that on non-prod stages.
        monkeypatch.setenv("APP_ENVIRONMENT", "test")
        old_singleton = storage_module._storage
        storage_module._storage = None
        try:
            with patch.object(
                storage_module.StorageClient, "ensure_bucket_exists"
            ) as mock_ensure:
                storage_module._get_storage_singleton()
            mock_ensure.assert_called_once()
        finally:
            storage_module._storage = old_singleton

    def test_skips_ensure_bucket_in_production(self, monkeypatch):
        # Regression: production's bucket must already exist for real - a
        # missing/mistyped bucket there should fail loudly, not get
        # silently "fixed" by creating an unexpected new one.
        monkeypatch.setenv("APP_ENVIRONMENT", "production")
        old_singleton = storage_module._storage
        storage_module._storage = None
        try:
            with patch.object(
                storage_module.StorageClient, "ensure_bucket_exists"
            ) as mock_ensure:
                storage_module._get_storage_singleton()
            mock_ensure.assert_not_called()
        finally:
            storage_module._storage = old_singleton


def _client_error(code: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "x"}}, operation)


def _storage(**kwargs) -> storage_module.StorageClient:
    return storage_module.StorageClient(
        endpoint_url="https://s3.amazonaws.com",
        access_key="testing",
        secret_key="testing",
        bucket="timeout-test-bucket",
        **kwargs,
    )


class TestClientTimeouts:
    """An object store that accepts a connection and never answers must not hold
    a request thread for minutes."""

    def test_the_client_has_short_timeouts_and_bounded_retries(self):
        config = _storage()._client.meta.config

        assert config.connect_timeout == 5
        assert config.read_timeout == 60
        assert config.retries["total_max_attempts"] == 3
        assert config.retries["mode"] == "standard"

    def test_the_public_client_has_the_same_limits(self):
        storage = _storage(public_endpoint_url="https://files.example.org")
        assert storage._public_client is not storage._client

        config = storage._public_client.meta.config

        assert config.connect_timeout == 5
        assert config.read_timeout == 60
        assert config.retries["total_max_attempts"] == 3

    def test_requests_are_still_signed_with_version_4(self):
        assert _storage()._client.meta.config.signature_version == "s3v4"


class TestExistsErrors:
    @pytest.mark.parametrize("code", ["404", "NoSuchKey", "NoSuchBucket", "NotFound"])
    def test_a_missing_object_is_reported_as_missing(self, code):
        storage = _storage()
        with patch.object(
            storage._client,
            "head_object",
            side_effect=_client_error(code, "HeadObject"),
        ):
            assert storage.exists("some/key") is False

    @pytest.mark.parametrize("code", ["AccessDenied", "403", "500", "SlowDown"])
    def test_any_other_error_is_raised_instead_of_reported_as_missing(self, code):
        storage = _storage()
        with (
            patch.object(
                storage._client,
                "head_object",
                side_effect=_client_error(code, "HeadObject"),
            ),
            pytest.raises(ClientError) as raised,
        ):
            storage.exists("some/key")

        assert raised.value.response["Error"]["Code"] == code

    def test_an_error_without_a_code_is_raised(self):
        storage = _storage()
        with (
            patch.object(
                storage._client,
                "head_object",
                side_effect=ClientError({}, "HeadObject"),
            ),
            pytest.raises(ClientError),
        ):
            storage.exists("some/key")


class TestEnsureBucketErrors:
    @pytest.mark.parametrize("code", ["404", "NoSuchBucket", "NotFound"])
    def test_a_missing_bucket_is_created(self, code):
        storage = _storage()
        with (
            patch.object(
                storage._client,
                "head_bucket",
                side_effect=_client_error(code, "HeadBucket"),
            ),
            patch.object(storage._client, "create_bucket") as mock_create,
        ):
            storage.ensure_bucket_exists()

        mock_create.assert_called_once_with(Bucket="timeout-test-bucket")

    @pytest.mark.parametrize("code", ["403", "AccessDenied", "500"])
    def test_any_other_error_is_raised_and_nothing_is_created(self, code):
        storage = _storage()
        with (
            patch.object(
                storage._client,
                "head_bucket",
                side_effect=_client_error(code, "HeadBucket"),
            ),
            patch.object(storage._client, "create_bucket") as mock_create,
            pytest.raises(ClientError) as raised,
        ):
            storage.ensure_bucket_exists()

        assert raised.value.response["Error"]["Code"] == code
        mock_create.assert_not_called()


def _grayscale_png(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    PILImage.new("L", (width, height)).save(buf, format="PNG")
    return buf.getvalue()


class TestPixelLimit:
    def test_the_limit_is_the_documented_value(self):
        assert PILImage.MAX_IMAGE_PIXELS == 50_000_000

    def test_a_file_above_twice_the_limit_is_refused(self):
        # 100,020,001 pixels in a few hundred bytes: far more than the limit
        # allows to be decoded.
        data = _grayscale_png(10_001, 10_001)
        assert len(data) < 1_000_000

        with pytest.raises(PILImage.DecompressionBombError):
            generate_thumbnail(data, 100)

    def test_a_large_photo_below_the_hard_cap_still_works_with_a_warning(self):
        data = _grayscale_png(8_000, 8_000)

        with pytest.warns(PILImage.DecompressionBombWarning):
            thumbnail, content_type = generate_thumbnail(data, 100)

        assert content_type == "image/jpeg"
        assert PILImage.open(io.BytesIO(thumbnail)).width == 100
