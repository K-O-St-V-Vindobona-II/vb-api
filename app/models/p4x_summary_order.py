import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models.types import FlexibleDate


class P4xSummaryOrder(Base):
    __tablename__ = "p4x_summary_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    ordered_by: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE", onupdate="CASCADE")
    )
    email: Mapped[str]
    summary_start: Mapped[datetime.date] = mapped_column(FlexibleDate)
    summary_end: Mapped[datetime.date] = mapped_column(FlexibleDate)
    pid: Mapped[str | None]
    started_at: Mapped[datetime.datetime | None]
    finished_at: Mapped[datetime.datetime | None]
    finished_ok: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime.datetime | None]
    updated_at: Mapped[datetime.datetime | None]
