from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, DateTime, Enum, ForeignKey, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.enums import RoleAssignmentAction, enum_values

if TYPE_CHECKING:
    from app.models.member import Member
    from app.models.role import Role


class RoleAssignmentEvent(Base):
    """One grant, revocation or period change of a member's role.

    Roles carry permissions (see PERMISSION_RULES), so this table is the
    history of who gave which permissions to whom, and when. It is
    append-only: a database trigger rejects every UPDATE and DELETE, and the
    foreign keys restrict deletion of the referenced rows instead of nulling
    them, so an entry never loses its subject, role or actor.
    """

    __tablename__ = "role_assignment_events"
    __table_args__ = (
        CheckConstraint(
            "enddate IS NULL OR startdate < enddate",
            name="role_assignment_events_startdate_enddate_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("members.id", ondelete="RESTRICT", onupdate="CASCADE"),
        index=True,
    )
    role_id: Mapped[str] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT", onupdate="CASCADE"),
        index=True,
    )
    action: Mapped[RoleAssignmentAction] = mapped_column(
        Enum(
            RoleAssignmentAction,
            name="role_assignment_action",
            native_enum=True,
            values_callable=enum_values,
        )
    )
    startdate: Mapped[date] = mapped_column(Date)
    # The end date after the event (NULL: open-ended). For a revocation this
    # is the end date the revoked entry had.
    enddate: Mapped[date | None] = mapped_column(Date)
    # Only set for a period change: the end date before it.
    previous_enddate: Mapped[date | None] = mapped_column(Date)
    # NULL for a change that no member made (a script, a future job).
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("members.id", ondelete="RESTRICT", onupdate="CASCADE"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    member: Mapped[Member] = relationship(foreign_keys=[member_id])
    role: Mapped[Role] = relationship()
    actor: Mapped[Member | None] = relationship(foreign_keys=[actor_id])
