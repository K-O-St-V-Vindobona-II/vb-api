from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Badge(Base):
    __tablename__ = "badges"
    __table_args__ = (
        CheckConstraint('"order" IS NULL OR "order" >= 0', name="badges_order_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None]
    group: Mapped[str | None]
    order: Mapped[int | None] = mapped_column(default=0)
