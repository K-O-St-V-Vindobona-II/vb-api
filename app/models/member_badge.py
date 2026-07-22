from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.badge import Badge


class MemberBadge(Base):
    __tablename__ = "badges_members"
    __table_args__ = (
        CheckConstraint(
            "presentationdate_accuracy IS NULL "
            "OR presentationdate_accuracy BETWEEN 0 AND 3",
            name="badges_members_presentationdate_accuracy_check",
        ),
    )

    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
    )
    badge_id: Mapped[int] = mapped_column(
        ForeignKey("badges.id", ondelete="RESTRICT", onupdate="CASCADE"),
        primary_key=True,
    )
    presentationdate: Mapped[datetime.date | None] = mapped_column(Date)
    presentationdate_accuracy: Mapped[int | None] = mapped_column(default=0)

    badge: Mapped[Badge] = relationship(lazy="joined")
