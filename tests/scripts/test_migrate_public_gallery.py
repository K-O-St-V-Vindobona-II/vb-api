"""Regression tests for scripts/migrate_public_gallery.py."""

import hashlib
import io
from unittest.mock import MagicMock, patch

from PIL import Image as PILImage

import scripts.migrate_public_gallery as migrate_public_gallery
from app.models.public_gallery_image import PublicGalleryImage
from tests.scripts._subprocess_helpers import (
    assert_module_imports_and_configures_mappers,
)

GALLERY_HTML = """
<html><body>
<div id="flickrGal0">
  <img decoding="async" alt="Ostermesse"
       src="https://farm66.static.flickr.com/65535/1_a.jpg"
       data-safe-src="https://farm66.static.flickr.com/65535/1_a.jpg" />
  <img decoding="async" alt="Fronleichnam"
       src="https://farm66.static.flickr.com/65535/2_b.jpg"
       data-safe-src="https://farm66.static.flickr.com/65535/2_b.jpg" />
</div>
<div id="flickrGal1">
  <!-- Same photostream rendered a second time - must be deduped by URL -->
  <img decoding="async" alt="Ostermesse"
       src="https://farm66.static.flickr.com/65535/1_a.jpg"
       data-safe-src="https://farm66.static.flickr.com/65535/1_a.jpg" />
  <img decoding="async" alt="Kein Alt-Text"
       src="https://farm66.static.flickr.com/65535/3_c.jpg" />
  <img decoding="async"
       src="https://farm66.static.flickr.com/65535/4_d.jpg" />
</div>
<img decoding="async" alt="Logo" src="https://www.vindobona2.at/vb/logo.png" />
</body></html>
"""


def _make_jpeg_bytes(width: int = 100, height: int = 80) -> bytes:
    img = PILImage.new("RGB", (width, height), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _mock_response(content: bytes | str, content_type: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    if isinstance(content, str):
        resp.text = content
    else:
        resp.content = content
    resp.headers = {"Content-Type": content_type} if content_type else {}
    return resp


def test_standalone_import_configures_mappers_without_error() -> None:
    assert_module_imports_and_configures_mappers("scripts.migrate_public_gallery")


class TestGalleryImageParser:
    def test_extracts_flickr_images_with_captions_in_order(self) -> None:
        parser = migrate_public_gallery._GalleryImageParser()
        parser.feed(GALLERY_HTML)

        assert parser.images == [
            ("https://farm66.static.flickr.com/65535/1_a.jpg", "Ostermesse"),
            ("https://farm66.static.flickr.com/65535/2_b.jpg", "Fronleichnam"),
            ("https://farm66.static.flickr.com/65535/3_c.jpg", "Kein Alt-Text"),
            ("https://farm66.static.flickr.com/65535/4_d.jpg", None),
        ]

    def test_deduplicates_by_url(self) -> None:
        parser = migrate_public_gallery._GalleryImageParser()
        parser.feed(GALLERY_HTML)
        urls = [url for url, _ in parser.images]
        assert len(urls) == len(set(urls))

    def test_ignores_non_flickr_images(self) -> None:
        parser = migrate_public_gallery._GalleryImageParser()
        parser.feed(GALLERY_HTML)
        assert all("static.flickr.com" in url for url, _ in parser.images)


class TestFetchGalleryImages:
    def test_fetches_and_parses_the_page(self) -> None:
        with patch.object(
            migrate_public_gallery.requests,
            "get",
            return_value=_mock_response(GALLERY_HTML),
        ) as mock_get:
            images = migrate_public_gallery.fetch_gallery_images(
                "https://www.vindobona2.at/vb/"
            )

        mock_get.assert_called_once_with(
            "https://www.vindobona2.at/vb/",
            timeout=migrate_public_gallery.REQUEST_TIMEOUT,
        )
        assert len(images) == 4


class TestMigrateImage:
    def test_successful_migration_inserts_row_and_uploads(
        self, db_session, mock_s3
    ) -> None:
        content = _make_jpeg_bytes(120, 90)
        with patch.object(
            migrate_public_gallery.requests,
            "get",
            return_value=_mock_response(content, "image/jpeg"),
        ):
            status = migrate_public_gallery.migrate_image(
                db_session,
                mock_s3,
                "https://farm66.static.flickr.com/65535/1_a.jpg",
                "Ostermesse",
                1,
                dry_run=False,
            )

        assert status.startswith("MIGRATED")
        img = db_session.query(PublicGalleryImage).one()
        assert img.caption == "Ostermesse"
        assert img.width == 120
        assert img.height == 90
        assert img.sort_order == 1
        assert img.is_published is True
        assert img.created_by is None

        key = f"{migrate_public_gallery.S3_PATH_PUBLIC_GALLERY}/{img.sha256_hash}"
        assert mock_s3.exists(key)

    def test_skips_already_migrated_image(self, db_session, mock_s3) -> None:
        content = _make_jpeg_bytes()
        with patch.object(
            migrate_public_gallery.requests,
            "get",
            return_value=_mock_response(content, "image/jpeg"),
        ):
            migrate_public_gallery.migrate_image(
                db_session, mock_s3, "https://x/1.jpg", "A", 1, dry_run=False
            )
            status = migrate_public_gallery.migrate_image(
                db_session, mock_s3, "https://x/1-again.jpg", "A", 2, dry_run=False
            )

        assert status.startswith("SKIP (already migrated)")
        assert db_session.query(PublicGalleryImage).count() == 1

    def test_skips_unsupported_content_type(self, db_session, mock_s3) -> None:
        with patch.object(
            migrate_public_gallery.requests,
            "get",
            return_value=_mock_response(b"gif-bytes", "image/gif"),
        ):
            status = migrate_public_gallery.migrate_image(
                db_session, mock_s3, "https://x/1.gif", "A", 1, dry_run=False
            )

        assert "unsupported content-type" in status
        assert db_session.query(PublicGalleryImage).count() == 0

    def test_skips_invalid_image_bytes(self, db_session, mock_s3) -> None:
        with patch.object(
            migrate_public_gallery.requests,
            "get",
            return_value=_mock_response(b"not-an-image", "image/jpeg"),
        ):
            status = migrate_public_gallery.migrate_image(
                db_session, mock_s3, "https://x/1.jpg", "A", 1, dry_run=False
            )

        assert "not a valid image" in status
        assert db_session.query(PublicGalleryImage).count() == 0

    def test_dry_run_does_not_write(self, db_session, mock_s3) -> None:
        content = _make_jpeg_bytes()
        with patch.object(
            migrate_public_gallery.requests,
            "get",
            return_value=_mock_response(content, "image/jpeg"),
        ):
            status = migrate_public_gallery.migrate_image(
                db_session, mock_s3, "https://x/1.jpg", "A", 1, dry_run=True
            )

        assert status.startswith("WOULD MIGRATE")
        assert db_session.query(PublicGalleryImage).count() == 0

    def test_does_not_reupload_if_s3_object_already_exists(
        self, db_session, mock_s3
    ) -> None:
        content = _make_jpeg_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        key = f"{migrate_public_gallery.S3_PATH_PUBLIC_GALLERY}/{sha256}"
        mock_s3.upload(key, content, "image/jpeg")

        with (
            patch.object(
                migrate_public_gallery.requests,
                "get",
                return_value=_mock_response(content, "image/jpeg"),
            ),
            patch.object(mock_s3, "upload") as mock_upload,
        ):
            status = migrate_public_gallery.migrate_image(
                db_session, mock_s3, "https://x/1.jpg", "A", 1, dry_run=False
            )

        assert status.startswith("MIGRATED")
        mock_upload.assert_not_called()


class TestMain:
    def _run_main(self, argv: list[str]) -> None:
        with patch("sys.argv", ["migrate_public_gallery.py", *argv]):
            migrate_public_gallery.main()

    def test_no_images_found_exits_early(self, capsys) -> None:
        with (
            patch.object(
                migrate_public_gallery, "fetch_gallery_images", return_value=[]
            ),
            patch.object(migrate_public_gallery, "SessionLocal") as mock_session,
        ):
            self._run_main([])

        mock_session.assert_not_called()
        assert "Nothing to migrate." in capsys.readouterr().out

    def test_orchestrates_migration_and_prints_summary(self, capsys) -> None:
        fake_db = MagicMock()
        fake_db.query.return_value.scalar.return_value = None

        with (
            patch.object(
                migrate_public_gallery,
                "fetch_gallery_images",
                return_value=[("https://x/1.jpg", "A"), ("https://x/2.jpg", "B")],
            ),
            patch.object(migrate_public_gallery, "SessionLocal", return_value=fake_db),
            patch.object(
                migrate_public_gallery, "get_storage", return_value=MagicMock()
            ),
            patch.object(
                migrate_public_gallery,
                "migrate_image",
                side_effect=[
                    "MIGRATED: https://x/1.jpg",
                    "SKIP (already migrated): https://x/2.jpg",
                ],
            ) as mock_migrate,
        ):
            self._run_main([])

        assert mock_migrate.call_count == 2
        fake_db.close.assert_called_once()
        out = capsys.readouterr().out
        assert "Found 2 unique Flickr-hosted image(s)" in out
        assert "Migrated: 1, Skipped: 1" in out

    def test_dry_run_flag_is_forwarded(self) -> None:
        fake_db = MagicMock()
        fake_db.query.return_value.scalar.return_value = 5

        with (
            patch.object(
                migrate_public_gallery,
                "fetch_gallery_images",
                return_value=[("https://x/1.jpg", "A")],
            ),
            patch.object(migrate_public_gallery, "SessionLocal", return_value=fake_db),
            patch.object(
                migrate_public_gallery, "get_storage", return_value=MagicMock()
            ),
            patch.object(
                migrate_public_gallery, "migrate_image", return_value="WOULD MIGRATE: x"
            ) as mock_migrate,
        ):
            self._run_main(["--dry-run"])

        _args, kwargs = mock_migrate.call_args
        assert kwargs["dry_run"] is True

    def test_source_url_is_forwarded(self) -> None:
        with patch.object(
            migrate_public_gallery, "fetch_gallery_images", return_value=[]
        ) as mock_fetch:
            self._run_main(["--source-url", "https://example.test/"])

        mock_fetch.assert_called_once_with("https://example.test/")
