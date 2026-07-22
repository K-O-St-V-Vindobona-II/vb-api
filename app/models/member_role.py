from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.types import FlexibleDate

if TYPE_CHECKING:
    from app.models.member import Member
    from app.models.role import Role


class MemberRole(Base):
    __tablename__ = "members_roles"
    __table_args__ = (
        CheckConstraint(
            "enddate IS NULL OR startdate < enddate",
            name="members_roles_startdate_enddate_check",
        ),
    )

    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[str] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT", onupdate="CASCADE"),
        primary_key=True,
    )
    startdate: Mapped[datetime.date] = mapped_column(FlexibleDate, primary_key=True)
    enddate: Mapped[datetime.date | None] = mapped_column(FlexibleDate)

    member: Mapped[Member] = relationship(back_populates="member_roles")
    role: Mapped[Role] = relationship(lazy="joined")
