from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class StandesdbImage(Base):
    __tablename__ = "standesdb_images"

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
