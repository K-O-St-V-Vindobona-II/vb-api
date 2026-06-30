from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.archive_file import ArchiveFile
    from app.models.member import Member


class ArchiveFileComment(Base):
    __tablename__ = "archive_file_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    archive_file_id: Mapped[int] = mapped_column(ForeignKey("archive_files.id"))
    content: Mapped[str | None]
    created_by: Mapped[int | None] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime | None]
    updated_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]

    archive_file: Mapped[ArchiveFile] = relationship(viewonly=True)
    member: Mapped[Member] = relationship(
        foreign_keys=[created_by],
        lazy="joined",
    )
