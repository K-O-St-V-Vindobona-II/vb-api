import uuid
from datetime import datetime

from sqlalchemy import DateTime, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class P4xSpecialcontact(Base):
    __tablename__ = "p4x_special_contacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    cn: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    @property
    def search_label(self) -> str:
        return f"Spezial: {self.cn or ''}"
