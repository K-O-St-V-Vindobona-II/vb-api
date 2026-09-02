import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# Fixed id of the single settings row this table will ever hold - enforced
# by the singleton_check CHECK constraint below. Unlike every other
# UUID-PK table, this value is not generated at runtime via
# `uuid.uuid7()`: there is and will only ever be exactly one row, so it
# gets a fixed literal instead, set by migration
# 5c9769a76def_public_site_settings_id_to_uuid.py and never changed again.
SETTINGS_ROW_ID = uuid.UUID("01a05cf3-e8f8-771b-9e7d-99181b476951")


class PublicSiteSettings(Base):
    """Singleton row of site-wide public site settings: the "Über uns"
    section's MKV video (heading + YouTube video id), the Programm
    section's Google Calendar id, and the gallery section's heading.

    All are scalar, site-wide values with no identity/relations of their
    own - a single-row settings table avoids duplicating the
    singleton-enforcement pattern (a CHECK pinning the primary key to one
    fixed value) across several near-empty tables. Only the YouTube video
    id / calendar id are stored, not the
    embed/ICS URLs derived from them (no computed values persisted, per
    this project's 3NF rule) - URL construction stays frontend-side logic.
    """

    __tablename__ = "public_site_settings"
    __table_args__ = (
        CheckConstraint(
            f"id = '{SETTINGS_ROW_ID}'",
            name="public_site_settings_singleton_check",
        ),
        CheckConstraint(
            "char_length(about_video_heading) > 0",
            name="public_site_settings_heading_check",
        ),
        CheckConstraint(
            "about_video_youtube_id ~ '^[A-Za-z0-9_-]{11}$'",
            name="public_site_settings_youtube_id_check",
        ),
        CheckConstraint(
            r"programm_calendar_id ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'",
            name="public_site_settings_calendar_id_check",
        ),
        CheckConstraint(
            "char_length(gallery_heading) > 0",
            name="public_site_settings_gallery_heading_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    about_video_heading: Mapped[str] = mapped_column(Text)
    about_video_youtube_id: Mapped[str] = mapped_column(Text)
    programm_calendar_id: Mapped[str] = mapped_column(Text)
    gallery_heading: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
