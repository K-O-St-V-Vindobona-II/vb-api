from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.types import FlexibleDate

if TYPE_CHECKING:
    from app.models.key import Key


class MemberKey(Base):
    __tablename__ = "members_keys"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), primary_key=True)
    key_id: Mapped[int] = mapped_column(ForeignKey("keys.id"), primary_key=True)
    presentationdate: Mapped[datetime.date | None] = mapped_column(FlexibleDate)
    presentationdate_accuracy: Mapped[int | None] = mapped_column(default=0)

    key: Mapped[Key] = relationship(lazy="joined")
