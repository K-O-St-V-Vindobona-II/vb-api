import uuid

from sqlalchemy import CheckConstraint, Enum, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models.enums import BadgeGroup, enum_values


class Badge(Base):
    __tablename__ = "badges"
    __table_args__ = (
        CheckConstraint('"order" IS NULL OR "order" >= 0', name="badges_order_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    name: Mapped[str | None]
    group: Mapped[BadgeGroup | None] = mapped_column(
        Enum(
            BadgeGroup,
            name="badge_group",
            native_enum=True,
            values_callable=enum_values,
        )
    )
    order: Mapped[int | None] = mapped_column(default=0)
