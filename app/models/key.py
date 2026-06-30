from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Key(Base):
    __tablename__ = "keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None]
