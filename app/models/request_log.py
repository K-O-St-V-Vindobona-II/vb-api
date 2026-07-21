from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    client_ip: Mapped[str]
    client_ips: Mapped[str | None]
    client_user_agent_id: Mapped[int | None]
    member_id: Mapped[int | None]
    request_method: Mapped[str]
    request_path: Mapped[str]
    request_input: Mapped[str | None]
    response_status: Mapped[int]
    response_content: Mapped[str | None]
    memory_usage: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
