from __future__ import annotations

import datetime
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.key import Key


class MemberKey(Base):
    __tablename__ = "keys_members"
    __table_args__ = (
        CheckConstraint(
            "presentationdate_accuracy IS NULL "
            "OR presentationdate_accuracy BETWEEN 0 AND 3",
            name="keys_members_presentationdate_accuracy_check",
        ),
    )

    # No surrogate id - the primary key is the column combination itself.
    # References members.id_uuid/keys.id_uuid, not their still-integer
    # id: members' own Final-Cutover is slice 32, keys' is slice 25.
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("members.id_uuid", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
    )
    key_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("keys.id_uuid", ondelete="RESTRICT", onupdate="CASCADE"),
        primary_key=True,
    )
    presentationdate: Mapped[datetime.date | None] = mapped_column(Date)
    presentationdate_accuracy: Mapped[int | None] = mapped_column(default=0)

    key: Mapped[Key] = relationship(lazy="joined")
