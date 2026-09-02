import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models.enums import AboutTabSlot, enum_values

# The three fixed slots this table will ever hold - enforced by the native
# about_tab_slot ENUM type. No admin CRUD to add/remove rows:
# about_tabs_service only ever GETs/PUTs these three by slot.
KNOWN_SLOTS = tuple(AboutTabSlot)


class PublicSiteAboutTab(Base):
    """One of the 3 fixed "Über uns" tabs on the public www.vindobona2.at
    site (Der Anfang / MKV / Heute) - title + body, both admin-editable.

    Uses a UUID primary key for schema-wide consistency (see
    ad4d9aeff7b8_public_site_content_ids_to_uuid.py), even though id is
    never itself exposed on any endpoint (lookups go by slot, not id, both
    in the public response shape and the admin router's path) and nothing
    else in the schema references it via FK.
    """

    __tablename__ = "public_site_about_tabs"
    __table_args__ = (
        CheckConstraint(
            "char_length(title) > 0", name="public_site_about_tabs_title_check"
        ),
        CheckConstraint(
            "char_length(body) > 0", name="public_site_about_tabs_body_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    slot: Mapped[AboutTabSlot] = mapped_column(
        Enum(
            AboutTabSlot,
            name="about_tab_slot",
            native_enum=True,
            values_callable=enum_values,
        ),
        unique=True,
    )
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
