from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.archive_file import ArchiveFile
    from app.models.archive_store_item import ArchiveStoreItem


class ArchiveFileVersion(Base):
    __tablename__ = "archive_file_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    archive_file_id: Mapped[int] = mapped_column(ForeignKey("archive_files.id"))
    archive_store_item_id: Mapped[int] = mapped_column(
        ForeignKey("archive_store_items.id")
    )
    active: Mapped[bool | None] = mapped_column(default=True)

    archive_file: Mapped[ArchiveFile] = relationship(back_populates="file_versions")
    store_item: Mapped[ArchiveStoreItem] = relationship(lazy="joined")
