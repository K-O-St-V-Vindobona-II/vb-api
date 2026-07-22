from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, String
from sqlalchemy.orm import DynamicMapped, Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.archive_file import ArchiveFile
    from app.models.archive_permission import ArchivePermission


class ArchiveDir(Base):
    __tablename__ = "archive_dirs"
    __table_args__ = (
        CheckConstraint(
            "archive_dir_id IS NULL OR archive_dir_id >= 0",
            name="archive_dirs_archive_dir_id_check",
        ),
        CheckConstraint(
            "length(name) BETWEEN 3 AND 64", name="archive_dirs_name_check"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None]
    archive_dir_id: Mapped[int | None] = mapped_column(default=0)
    recursive_permissions: Mapped[bool | None] = mapped_column(default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    children: DynamicMapped[ArchiveDir] = relationship(
        foreign_keys="ArchiveDir.archive_dir_id",
        primaryjoin="ArchiveDir.id == foreign(ArchiveDir.archive_dir_id)",
        lazy="dynamic",
        viewonly=True,
    )
    parent: Mapped[ArchiveDir | None] = relationship(
        foreign_keys="ArchiveDir.archive_dir_id",
        primaryjoin="foreign(ArchiveDir.archive_dir_id) == ArchiveDir.id",
        remote_side="ArchiveDir.id",
        uselist=False,
        lazy="joined",
        join_depth=1,
        viewonly=True,
    )
    archive_files: DynamicMapped[ArchiveFile] = relationship(
        foreign_keys="ArchiveFile.archive_dir_id",
        primaryjoin="ArchiveDir.id == foreign(ArchiveFile.archive_dir_id)",
        lazy="dynamic",
        viewonly=True,
    )
    archive_permissions: Mapped[list[ArchivePermission]] = relationship(
        back_populates="archive_dir",
        cascade="all, delete-orphan",
        lazy="joined",
    )
