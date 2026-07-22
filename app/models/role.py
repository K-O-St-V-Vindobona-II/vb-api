from sqlalchemy import CheckConstraint, Enum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models.enums import RoleGroup, enum_values


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint('"order" IS NULL OR "order" >= 0', name="roles_order_check"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    group: Mapped[RoleGroup | None] = mapped_column(
        Enum(
            RoleGroup,
            name="role_group",
            native_enum=True,
            values_callable=enum_values,
        )
    )
    label: Mapped[str | None]
    order: Mapped[int | None] = mapped_column(default=0)
