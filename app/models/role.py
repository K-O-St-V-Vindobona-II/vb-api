from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(primary_key=True)
    group: Mapped[str | None]
    label: Mapped[str | None]
    order: Mapped[int | None] = mapped_column(default=0)
