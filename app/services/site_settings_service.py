from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from app.models.public_site_settings import SETTINGS_ROW_ID, PublicSiteSettings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def get_settings(db: Session) -> PublicSiteSettings:
    settings = db.get(PublicSiteSettings, SETTINGS_ROW_ID)
    if settings is None:
        # Seeded by the migration that creates this table - only reachable
        # if that migration was skipped/rolled back incorrectly.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Site-Settings fehlen.",
        )
    return settings


def update_settings(
    db: Session,
    settings: PublicSiteSettings,
    *,
    heading: str,
    youtube_id: str,
    calendar_id: str,
    gallery_heading: str,
) -> None:
    settings.about_video_heading = heading
    settings.about_video_youtube_id = youtube_id
    settings.programm_calendar_id = calendar_id
    settings.gallery_heading = gallery_heading
    db.commit()
