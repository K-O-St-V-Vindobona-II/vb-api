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

    id: Mapped[int] = mapped_column(primary_key=True)
    # Additive prep column for the schema-wide UUID-PK migration (see
    # 673aa46dc3b3_archive_files_phase_a_and_archive_.py) - not yet the
    # primary key. Final-Cutover follows in a later slice.
    id_uuid: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid7)
    archive_dir_id: Mapped[int | None] = mapped_column(default=0)
    description: Mapped[str | None]
    # References archive_store_items.id_uuid, not archive_store_items.id -
    # archive_store_items itself won't have a UUID primary key until its
    # own Final-Cutover, see 673aa46dc3b3's docstring.
    archive_store_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "archive_store_items.id_uuid", ondelete="RESTRICT", onupdate="CASCADE"
        )
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
