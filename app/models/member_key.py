from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.key import Key


class MemberKey(Base):
    __tablename__ = "members_keys"
    __table_args__ = (
        CheckConstraint(
            "presentationdate_accuracy IS NULL "
            "OR presentationdate_accuracy BETWEEN 0 AND 3",
            name="members_keys_presentationdate_accuracy_check",
        ),
    )

    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
    )
    key_id: Mapped[int] = mapped_column(
        ForeignKey("keys.id", ondelete="RESTRICT", onupdate="CASCADE"),
        primary_key=True,
    )
    presentationdate: Mapped[datetime.date | None] = mapped_column(Date)
    presentationdate_accuracy: Mapped[int | None] = mapped_column(default=0)

    key: Mapped[Key] = relationship(lazy="joined")
