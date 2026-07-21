from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class MembersLog(Base):
    __tablename__ = "members_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    modified_at: Mapped[datetime | None]
    modified_by: Mapped[int | None]
    member_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL", onupdate="CASCADE")
    )
    action: Mapped[str]
    key: Mapped[str]
    old: Mapped[str | None]
    new: Mapped[str | None]
