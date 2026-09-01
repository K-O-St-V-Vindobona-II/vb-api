import uuid

from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ClientUserAgent(Base):
    __tablename__ = "client_user_agents"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # Additive prep column for the schema-wide UUID-PK migration (see
    # a908d5613d52_members_and_client_user_agents_id_uuid_.py) - not yet
    # the primary key. request_logs.client_user_agent_id (a bare integer
    # column without a real FK today) cuts over onto this in slice 22,
    # bundled with request_logs' own Final-Cutover.
    id_uuid: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid7)
    string: Mapped[str] = mapped_column(unique=True)
