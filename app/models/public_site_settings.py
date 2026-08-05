from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, SmallInteger, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# Fixed id of the single settings row this table will ever hold - enforced
# by the singleton_check CHECK constraint below.
SETTINGS_ROW_ID = 1


class PublicSiteSettings(Base):
    """Singleton row of site-wide public site settings: the "Über uns"
    section's MKV video (heading + YouTube video id), the Programm
    section's Google Calendar id, and the gallery section's heading.

    All are scalar, site-wide values with no identity/relations of their
    own - a single-row settings table avoids duplicating the
    singleton-enforcement pattern (CHECK id=1) across several near-empty
    tables. Only the YouTube video id / calendar id are stored, not the
    embed/ICS URLs derived from them (no computed values persisted, see
    CLAUDE.md 3NF rule) - URL construction stays frontend-side logic.
    """

    __tablename__ = "public_site_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="public_site_settings_singleton_check"),
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

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
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
