from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Org(Base):
    __tablename__ = "orgs"
    __table_args__ = (
        CheckConstraint('"order" IS NULL OR "order" >= 0', name="orgs_order_check"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    label: Mapped[str | None]
    order: Mapped[int | None] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
