import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models.enums import BadgeGroup, enum_values


class Badge(Base):
    __tablename__ = "badges"
    __table_args__ = (
        CheckConstraint(
            "sort_order IS NULL OR sort_order >= 0", name="badges_sort_order_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    name: Mapped[str]
    group: Mapped[BadgeGroup | None] = mapped_column(
        Enum(
            BadgeGroup,
            name="badge_group",
            native_enum=True,
            values_callable=enum_values,
        )
    )
    sort_order: Mapped[int | None] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
