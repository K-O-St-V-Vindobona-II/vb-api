import uuid

from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class P4xSpecialcontact(Base):
    __tablename__ = "p4x_special_contacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    cn: Mapped[str | None]

    @property
    def search_label(self) -> str:
        return f"Spezial: {self.cn or ''}"
