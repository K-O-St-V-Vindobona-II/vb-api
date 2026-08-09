from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.enums import MemberChangeRequestStatus, enum_values

if TYPE_CHECKING:
    from app.models.member import Member


class MemberChangeRequest(Base):
    """A member's self-service proposal to change their own Stammdaten,
    pending review by their org's standesdb admin(s).

    Only the fields that actually differ from the member's live values are
    stored in proposed_data - not a full form snapshot. field_decisions is
    populated exactly once, atomically, when an admin resolves the request
    in a single review session (see
    app/services/member_change_request_service.py::resolve_change_request)
    - there is no persisted "partially decided" state, per explicit product
    decision.

    Plain integer id, not UUID: never exposed on a public/unauthenticated
    endpoint (only to the owning member and their org admins), same
    reasoning as app/models/scheduled_task_run.py.
    """

    __tablename__ = "member_change_requests"
    __table_args__ = (
        CheckConstraint(
            "(status = 'pending' AND field_decisions IS NULL"
            " AND resolved_at IS NULL)"
            " OR (status = 'resolved' AND field_decisions IS NOT NULL"
            " AND resolved_at IS NOT NULL)",
            name="member_change_requests_resolution_consistency_check",
        ),
        CheckConstraint(
            "proposed_data <> '{}'::jsonb",
            name="member_change_requests_proposed_data_not_empty_check",
        ),
        # At most one open request per member. resolved_by is deliberately
        # NOT part of the CHECK above (unlike field_decisions/resolved_at) -
        # members are hard-deleted in this system, and resolved_by ON DELETE
        # SET NULL must be able to null it out on a resolved row without
        # tripping a CHECK that demands it stay set. It's best-effort audit
        # metadata, same trust level as MembersLog.modified_by.
        Index(
            "member_change_requests_member_pending_uniq",
            "member_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE", onupdate="CASCADE"),
        index=True,
    )
    status: Mapped[MemberChangeRequestStatus] = mapped_column(
        Enum(
            MemberChangeRequestStatus,
            name="member_change_request_status",
            native_enum=True,
            values_callable=enum_values,
        ),
        default=MemberChangeRequestStatus.PENDING,
    )
    proposed_data: Mapped[dict[str, object]] = mapped_column(JSONB)
    field_decisions: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL", onupdate="CASCADE"),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    member: Mapped[Member] = relationship(
        foreign_keys=[member_id],
        lazy="joined",
    )
    resolver: Mapped[Member | None] = relationship(
        foreign_keys=[resolved_by],
        lazy="select",
    )
