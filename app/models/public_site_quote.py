import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PublicSiteQuote(Base):
    """One testimonial quote shown on the public site's "Zitate" section."""

    __tablename__ = "public_site_quotes"
    __table_args__ = (
        CheckConstraint(
            "char_length(quote) > 0", name="public_site_quotes_quote_check"
        ),
        CheckConstraint(
            "char_length(author) > 0", name="public_site_quotes_author_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    quote: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
