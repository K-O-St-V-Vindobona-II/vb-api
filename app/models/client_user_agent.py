from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ClientUserAgent(Base):
    __tablename__ = "client_user_agents"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    string: Mapped[str] = mapped_column(unique=True)
