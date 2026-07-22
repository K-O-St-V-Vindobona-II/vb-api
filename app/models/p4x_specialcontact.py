from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class P4xSpecialcontact(Base):
    __tablename__ = "p4x_special_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    cn: Mapped[str | None]

    @property
    def search_label(self) -> str:
        return f"Spezial: {self.cn or ''}"
