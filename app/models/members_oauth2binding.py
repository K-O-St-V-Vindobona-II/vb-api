from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.member import Member


class MembersOauth2Binding(Base):
    __tablename__ = "members_oauth2bindings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE", onupdate="CASCADE"),
        index=True,
    )

    provider: Mapped[str]
    remote_id: Mapped[str]
    remote_name: Mapped[str]
    bound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    lastuse_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    member: Mapped[Member] = relationship(back_populates="oauth_bindings")
