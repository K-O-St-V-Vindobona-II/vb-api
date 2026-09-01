from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Computed, DateTime, String, Uuid
from sqlalchemy.dialects.postgresql import TSVECTOR
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
    # Additive prep column for the schema-wide UUID-PK migration (see
    # 03e35e3c34bc_archive_dirs_id_uuid_phase_a.py) - not yet the primary
    # key. The self-referencing `archive_dir_id` column below is
    # deliberately left untouched until the Final-Cutover slice, which
    # also converts its `0`-sentinel to NULL.
    id_uuid: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid7)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None]
    archive_dir_id: Mapped[int | None] = mapped_column(default=0)
    recursive_permissions: Mapped[bool | None] = mapped_column(default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Postgres-maintained (GENERATED ALWAYS AS ... STORED, see migration
    # 10efdd07c37e) - name weighted above description. Never written from
    # Python, only read via full-text @@/ts_rank() in
    # archive_service.search_archive().
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('german', coalesce(name, '')), 'A') || "
            "setweight(to_tsvector('german', coalesce(description, '')), 'B')",
            persisted=True,
        ),
    )

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
