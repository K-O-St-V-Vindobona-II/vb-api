import uuid
from datetime import datetime

from sqlalchemy import DateTime, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ClientUserAgent(Base):
    __tablename__ = "client_user_agents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    string: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
