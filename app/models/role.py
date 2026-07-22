from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint('"order" IS NULL OR "order" >= 0', name="roles_order_check"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    group: Mapped[str | None]
    label: Mapped[str | None]
    order: Mapped[int | None] = mapped_column(default=0)
