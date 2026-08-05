from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# The three fixed slots this table will ever hold - enforced by the
# slot_check CHECK constraint in the migration. No admin CRUD to add/remove
# rows: about_tabs_service only ever GETs/PUTs these three by slot.
KNOWN_SLOTS = ("anfang", "mkv", "heute")


class PublicSiteAboutTab(Base):
    """One of the 3 fixed "Über uns" tabs on the public www.vindobona2.at
    site (Der Anfang / MKV / Heute) - title + body, both admin-editable.

    Plain integer id, not UUID: id is never exposed on the public endpoint
    (the public response is shaped by slot, not by id) and nothing else in
    the schema references it via FK.
    """

    __tablename__ = "public_site_about_tabs"
    __table_args__ = (
        CheckConstraint(
            "slot IN ('anfang', 'mkv', 'heute')",
            name="public_site_about_tabs_slot_check",
        ),
        CheckConstraint(
            "char_length(title) > 0", name="public_site_about_tabs_title_check"
        ),
        CheckConstraint(
            "char_length(body) > 0", name="public_site_about_tabs_body_check"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slot: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
