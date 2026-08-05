from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PublicSiteSocialLink(Base):
    """One social media link shown in the public site's nav/footer.

    `platform` is a free (format-checked) slug, not a CHECK-enum with a
    fixed value list - new platforms (e.g. LinkedIn) must be addable
    without a schema migration. It's used as a frontend icon-lookup key
    with a generic fallback, never rendered as raw markup (no icon SVG
    stored here - that would force v-html on the unauthenticated public
    site, which this codebase's ESLint config forbids outright).
    """

    __tablename__ = "public_site_social_links"
    __table_args__ = (
        CheckConstraint(
            "platform ~ '^[a-z][a-z0-9_]*$'",
            name="public_site_social_links_platform_check",
        ),
        CheckConstraint(
            "char_length(label) > 0", name="public_site_social_links_label_check"
        ),
        CheckConstraint(
            "url ~* '^https?://'", name="public_site_social_links_url_check"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
