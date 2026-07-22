from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.archive_file import ArchiveFile
    from app.models.member import Member


class ArchiveFileComment(Base):
    __tablename__ = "archive_file_comments"
    __table_args__ = (
        CheckConstraint(
            "content IS NULL OR length(content) BETWEEN 1 AND 1000",
            name="archive_file_comments_content_check",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    archive_file_id: Mapped[int] = mapped_column(
        ForeignKey("archive_files.id", ondelete="CASCADE", onupdate="CASCADE")
    )
    content: Mapped[str | None]
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL", onupdate="CASCADE")
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    archive_file: Mapped[ArchiveFile] = relationship(viewonly=True)
    member: Mapped[Member] = relationship(
        foreign_keys=[created_by],
        lazy="joined",
    )
