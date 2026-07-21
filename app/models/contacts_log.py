from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ContactsLog(Base):
    __tablename__ = "contacts_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_by: Mapped[int | None]
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL", onupdate="CASCADE")
    )
    action: Mapped[str]
    key: Mapped[str]
    old: Mapped[str | None]
    new: Mapped[str | None]
