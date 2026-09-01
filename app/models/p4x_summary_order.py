import datetime
import uuid

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class P4xSummaryOrder(Base):
    __tablename__ = "p4x_summary_orders"
    __table_args__ = (
        CheckConstraint(
            "summary_end >= summary_start",
            name="p4x_summary_orders_summary_start_end_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    # References members.id_uuid, not members.id - members itself won't
    # have a UUID primary key until its own Final-Cutover (slice 32).
    ordered_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("members.id_uuid", ondelete="CASCADE", onupdate="CASCADE")
    )
    email: Mapped[str]
    summary_start: Mapped[datetime.date] = mapped_column(Date)
    summary_end: Mapped[datetime.date] = mapped_column(Date)
    pid: Mapped[str | None]
    started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    finished_ok: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
