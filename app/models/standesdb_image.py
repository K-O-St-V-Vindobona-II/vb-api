from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class StandesdbImage(Base):
    __tablename__ = "standesdb_images"
    __table_args__ = (
        CheckConstraint(
            "size IS NULL OR size >= 0", name="standesdb_images_size_check"
        ),
        CheckConstraint(
            "width IS NULL OR width > 0", name="standesdb_images_width_check"
        ),
        CheckConstraint(
            "height IS NULL OR height > 0", name="standesdb_images_height_check"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    owner_type: Mapped[str]
    owner_id: Mapped[int]
    extension: Mapped[str | None]
    type: Mapped[str | None]
    size: Mapped[int | None]
    height: Mapped[int | None]
    width: Mapped[int | None]
    sha256_hash: Mapped[str]
    description: Mapped[str | None]
    default: Mapped[int | None] = mapped_column(default=0)
    created_by: Mapped[int | None]
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
