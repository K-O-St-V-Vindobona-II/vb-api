import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PublicGalleryImage(Base):
    """An image shown in the public www.vindobona2.at gallery section.

    Uses a UUID primary key (unlike most other models in this codebase,
    which use integer PKs) since this is a brand-new table with no legacy
    data to migrate, following CLAUDE.md's UUID-for-new-tables guidance.
    """

    __tablename__ = "public_gallery_images"
    __table_args__ = (
        CheckConstraint("size >= 0", name="public_gallery_images_size_check"),
        CheckConstraint(
            "width > 0 AND height > 0",
            name="public_gallery_images_width_height_check",
        ),
        CheckConstraint(
            "sort_order >= 0", name="public_gallery_images_sort_order_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sha256_hash: Mapped[str] = mapped_column(unique=True)
    extension: Mapped[str]
    content_type: Mapped[str]
    size: Mapped[int]
    width: Mapped[int]
    height: Mapped[int]
    caption: Mapped[str | None]
    sort_order: Mapped[int] = mapped_column(index=True)
    is_published: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
