from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import DynamicMapped, Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.archive_dir import ArchiveDir
    from app.models.archive_file_comment import ArchiveFileComment
    from app.models.archive_file_version import ArchiveFileVersion


class ArchiveFile(Base):
    __tablename__ = "archive_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    archive_dir_id: Mapped[int | None] = mapped_column(default=0)
    description: Mapped[str | None]
    deleted_at: Mapped[datetime | None]

    archive_dir: Mapped[ArchiveDir | None] = relationship(
        foreign_keys="ArchiveFile.archive_dir_id",
        primaryjoin="foreign(ArchiveFile.archive_dir_id) == ArchiveDir.id",
        uselist=False,
        lazy="joined",
        viewonly=True,
    )
    file_versions: Mapped[list[ArchiveFileVersion]] = relationship(
        back_populates="archive_file",
        lazy="joined",
    )
    comments: DynamicMapped[ArchiveFileComment] = relationship(
        back_populates="archive_file",
        lazy="dynamic",
        viewonly=True,
    )
