from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.member import Member


class ArchiveStoreItem(Base):
    __tablename__ = "archive_store_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_name: Mapped[str | None]
    original_description: Mapped[str | None]
    name: Mapped[str]
    description: Mapped[str | None]
    extension: Mapped[str]
    mime_type: Mapped[str]
    size: Mapped[int]
    sha256_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL", onupdate="CASCADE")
    )
    backedup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    member: Mapped[Member] = relationship(
        foreign_keys=[created_by],
        lazy="joined",
    )

    @property
    def is_image(self) -> bool:
        return (
            self.mime_type.startswith("image/") and "eps" not in self.mime_type.lower()
        )
