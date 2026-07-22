from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class State(Base):
    __tablename__ = "states"
    __table_args__ = (
        CheckConstraint('"order" IS NULL OR "order" >= 0', name="states_order_check"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    label: Mapped[str | None]
    order: Mapped[int | None] = mapped_column(default=0)
