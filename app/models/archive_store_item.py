from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.member import Member


class ArchiveStoreItem(Base):
    __tablename__ = "archive_store_items"
    __table_args__ = (
        CheckConstraint("size >= 0", name="archive_store_items_size_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    description: Mapped[str | None]
    extension: Mapped[str]
    mime_type: Mapped[str]
    size: Mapped[int]
    sha256_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL", onupdate="CASCADE")
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Postgres-maintained (GENERATED ALWAYS AS ... STORED, see migration
    # 10efdd07c37e) - name weighted above extension. Never written from
    # Python, only read via full-text @@/ts_rank() in
    # archive_service.search_archive().
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('german', coalesce(name, '')), 'A') || "
            "setweight(to_tsvector('german', coalesce(extension, '')), 'C')",
            persisted=True,
        ),
    )

    member: Mapped[Member] = relationship(
        foreign_keys=[created_by],
        lazy="joined",
    )

    @property
    def is_image(self) -> bool:
        return (
            self.mime_type.startswith("image/") and "eps" not in self.mime_type.lower()
        )
