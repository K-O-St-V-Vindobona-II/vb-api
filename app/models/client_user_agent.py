import uuid

from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ClientUserAgent(Base):
    __tablename__ = "client_user_agents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    string: Mapped[str] = mapped_column(unique=True)
