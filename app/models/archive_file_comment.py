from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKey, Uuid
from sqlalchemy.dialects.postgresql import TSVECTOR
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

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    archive_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("archive_files.id", ondelete="CASCADE", onupdate="CASCADE")
    )
    content: Mapped[str | None]
    # References members.id_uuid, not members.id - members itself won't
    # have a UUID primary key until its own Final-Cutover.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("members.id_uuid", ondelete="SET NULL", onupdate="CASCADE")
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Postgres-maintained (GENERATED ALWAYS AS ... STORED, see migration
    # 10efdd07c37e) - lowest search weight of all four tables, a comment
    # mentioning something is a weaker signal than the item's own name/
    # description. Never written from Python, only read via full-text
    # @@/ts_rank() in archive_service.search_archive().
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('german', coalesce(content, '')), 'D')",
            persisted=True,
        ),
    )

    archive_file: Mapped[ArchiveFile] = relationship(viewonly=True)
    member: Mapped[Member] = relationship(
        foreign_keys=[created_by],
        lazy="joined",
    )
