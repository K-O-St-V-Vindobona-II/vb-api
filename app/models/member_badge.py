from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.types import FlexibleDate

if TYPE_CHECKING:
    from app.models.badge import Badge


class MemberBadge(Base):
    __tablename__ = "members_badges"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), primary_key=True)
    badge_id: Mapped[int] = mapped_column(ForeignKey("badges.id"), primary_key=True)
    presentationdate: Mapped[datetime.date | None] = mapped_column(FlexibleDate)
    presentationdate_accuracy: Mapped[int | None] = mapped_column(default=0)

    badge: Mapped[Badge] = relationship(lazy="joined")
