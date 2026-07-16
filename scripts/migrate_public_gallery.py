#!/usr/bin/env python3
"""One-time migration: legacy "Eindrücke" gallery -> public_gallery_images.

The legacy www.vindobona2.at "Eindrücke" section is backed by a real Flickr
account's photostream (via the "Flickr Justified Gallery" WordPress plugin),
not local WordPress uploads — see project research notes. This script scrapes
the live page for those Flickr-hosted images, downloads each one, and inserts
it into the new `public_gallery_images` table (app/models/public_gallery_image.py)
— the table that now backs the new vb-www site's own Eindrücke section via
`GET /api/public/gallery`.

This is a one-time snapshot: after it runs, the public gallery is fully
decoupled from Flickr — editors manage it from then on via vb-intern's
"www-Administration" -> "Galerie".

Usage:
    python scripts/migrate_public_gallery.py
    python scripts/migrate_public_gallery.py --dry-run
    python scripts/migrate_public_gallery.py --source-url https://www.vindobona2.at/vb/
"""

import argparse
import hashlib
import io
import sys
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import requests
from PIL import Image as PILImage
from sqlalchemy import func
from sqlalchemy.orm import Session

_VB_API_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VB_API_ROOT))

import app.db.base  # noqa: F401 — registers all models  # pyright: ignore[reportUnusedImport]
from app.core.storage import S3_PATH_PUBLIC_GALLERY, StorageClient, get_storage
from app.db.database import SessionLocal
from app.models.public_gallery_image import PublicGalleryImage

DEFAULT_SOURCE_URL = "https://www.vindobona2.at/vb/"
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png"}
REQUEST_TIMEOUT = 30


class _GalleryImageParser(HTMLParser):
    """Extracts (url, caption) pairs for every <img> whose src/data-safe-src
    points at Flickr's CDN (static.flickr.com) - that's how the legacy
    "Eindrücke" section's Flickr-Justified-Gallery plugin serves photos.
    Dedupes by URL (the page renders the same photostream in more than one
    place), preserving first-seen order for a stable sort_order."""

    def __init__(self) -> None:
        super().__init__()
        self.images: list[tuple[str, str | None]] = []
        self._seen_urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            return
        attr_dict = dict(attrs)
        url = attr_dict.get("data-safe-src") or attr_dict.get("src")
        if not url or "static.flickr.com" not in url:
            return
        if url in self._seen_urls:
            return
        self._seen_urls.add(url)
        self.images.append((url, attr_dict.get("alt")))


def fetch_gallery_images(source_url: str) -> list[tuple[str, str | None]]:
    """Downloads `source_url` and returns the Flickr-hosted (url, caption)
    pairs found on it, in page order."""
    resp = requests.get(source_url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    parser = _GalleryImageParser()
    parser.feed(resp.text)
    return parser.images


def migrate_image(
    db: Session,
    storage: StorageClient,
    url: str,
    caption: str | None,
    sort_order: int,
    *,
    dry_run: bool,
) -> str:
    """Downloads one image and inserts it into public_gallery_images.
    Returns a human-readable status line for the summary report."""
    resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    content = resp.content
    content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()

    if content_type not in ALLOWED_CONTENT_TYPES:
        return f"SKIP (unsupported content-type '{content_type}'): {url}"

    sha256 = hashlib.sha256(content).hexdigest()

    existing = db.query(PublicGalleryImage).filter_by(sha256_hash=sha256).first()
    if existing:
        return f"SKIP (already migrated): {url}"

    try:
        pil_img = PILImage.open(io.BytesIO(content))
        width, height = pil_img.size
    except (OSError, ValueError) as exc:
        return f"SKIP (not a valid image, {exc}): {url}"

    if dry_run:
        return (
            f"WOULD MIGRATE: {url} "
            f"(caption={caption!r}, {width}x{height}, {len(content)} bytes)"
        )

    ext = content_type.split("/")[-1]
    if ext == "jpeg":
        ext = "jpg"

    key = f"{S3_PATH_PUBLIC_GALLERY}/{sha256}"
    if not storage.exists(key):
        storage.upload(key, content, content_type)

    now = datetime.now(UTC)
    db.add(
        PublicGalleryImage(
            sha256_hash=sha256,
            extension=ext,
            content_type=content_type,
            size=len(content),
            width=width,
            height=height,
            caption=caption,
            sort_order=sort_order,
            is_published=True,
            created_by=None,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return f"MIGRATED: {url} (caption={caption!r})"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate the legacy Flickr-backed 'Eindrücke' gallery into "
            "public_gallery_images."
        ),
    )
    parser.add_argument(
        "--source-url",
        default=DEFAULT_SOURCE_URL,
        help=f"Page to scrape for gallery images (default: {DEFAULT_SOURCE_URL})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be migrated, without writing to S3/the database",
    )
    args = parser.parse_args()

    print(f"Fetching gallery images from {args.source_url} ...")
    images = fetch_gallery_images(args.source_url)
    print(f"Found {len(images)} unique Flickr-hosted image(s) on the page.\n")

    if not images:
        print("Nothing to migrate.")
        return

    db = SessionLocal()
    storage = get_storage()

    next_sort_order = (
        db.query(func.max(PublicGalleryImage.sort_order)).scalar() or 0
    ) + 1

    migrated = 0
    skipped = 0
    for offset, (url, caption) in enumerate(images):
        status = migrate_image(
            db,
            storage,
            url,
            caption,
            next_sort_order + offset,
            dry_run=args.dry_run,
        )
        print(f"  {status}")
        if status.startswith("MIGRATED"):
            migrated += 1
        elif status.startswith("SKIP"):
            skipped += 1

    db.close()
    prefix = "DRY RUN — " if args.dry_run else ""
    print(f"\n=== {prefix}Migrated: {migrated}, Skipped: {skipped} ===")


if __name__ == "__main__":
    main()
