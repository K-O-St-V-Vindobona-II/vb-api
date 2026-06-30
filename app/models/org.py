from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[str] = mapped_column(primary_key=True)
    label: Mapped[str | None]
    order: Mapped[int | None] = mapped_column(default=0)
