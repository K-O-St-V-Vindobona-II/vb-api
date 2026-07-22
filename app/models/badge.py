from sqlalchemy import CheckConstraint, Enum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models.enums import BadgeGroup, enum_values


class Badge(Base):
    __tablename__ = "badges"
    __table_args__ = (
        CheckConstraint('"order" IS NULL OR "order" >= 0', name="badges_order_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
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
