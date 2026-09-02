from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKey, Uuid
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DynamicMapped, Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.archive_dir import ArchiveDir
    from app.models.archive_file_comment import ArchiveFileComment
    from app.models.archive_store_item import ArchiveStoreItem


class ArchiveFile(Base):
    __tablename__ = "archive_files"
    __table_args__ = (
        CheckConstraint(
            "archive_dir_id IS NULL OR archive_dir_id >= 0",
            name="archive_files_archive_dir_id_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    archive_dir_id: Mapped[int | None] = mapped_column(default=0)
    description: Mapped[str | None]
    archive_store_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("archive_store_items.id", ondelete="RESTRICT", onupdate="CASCADE")
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Postgres-maintained (GENERATED ALWAYS AS ... STORED, see migration
    # 10efdd07c37e) - the file's name/extension live on ArchiveStoreItem
    # instead, which has its own search_vector. Never written from Python,
    # only read via full-text @@/ts_rank() in
    # archive_service.search_archive().
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('german', coalesce(description, '')), 'B')",
            persisted=True,
        ),
    )

    archive_dir: Mapped[ArchiveDir | None] = relationship(
        foreign_keys="ArchiveFile.archive_dir_id",
        primaryjoin="foreign(ArchiveFile.archive_dir_id) == ArchiveDir.id",
        uselist=False,
        lazy="joined",
        viewonly=True,
    )
    store_item: Mapped[ArchiveStoreItem] = relationship(lazy="joined")
    comments: DynamicMapped[ArchiveFileComment] = relationship(
        back_populates="archive_file",
        lazy="dynamic",
        viewonly=True,
    )
