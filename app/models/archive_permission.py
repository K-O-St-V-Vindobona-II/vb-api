from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.archive_dir import ArchiveDir


class ArchivePermission(Base):
    __tablename__ = "archive_permissions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    # References archive_dirs.id_uuid, not archive_dirs.id - archive_dirs
    # itself won't have a UUID primary key until its own Final-Cutover, see
    # 673aa46dc3b3's docstring.
    archive_dir_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("archive_dirs.id_uuid", ondelete="CASCADE", onupdate="CASCADE")
    )
    org_id: Mapped[str]
    state_id: Mapped[str]

    archive_dir: Mapped[ArchiveDir] = relationship(
        back_populates="archive_permissions",
    )
