from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models.enums import ChangeLogAction, enum_values


class ContactsLog(Base):
    __tablename__ = "contacts_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_by: Mapped[int | None]
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL", onupdate="CASCADE")
    )
    action: Mapped[ChangeLogAction] = mapped_column(
        Enum(
            ChangeLogAction,
            name="changelog_action",
            native_enum=True,
            values_callable=enum_values,
        )
    )
    key: Mapped[str]
    old: Mapped[str | None]
    new: Mapped[str | None]
