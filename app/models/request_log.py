import uuid
from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class RequestLog(Base):
    """Written by app.core.activity_logger.ActivityLoggingMiddleware for
    every non-excluded request. created_at/updated_at are populated by the
    database (DEFAULT now() / the shared set_updated_at() trigger) - never
    assigned from Python."""

    __tablename__ = "request_logs"
    __table_args__ = (
        CheckConstraint("memory_usage >= 0", name="request_logs_memory_usage_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    client_ip: Mapped[str]
    client_ips: Mapped[list[str]] = mapped_column(JSONB)
    client_user_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("client_user_agents.id", ondelete="SET NULL", onupdate="CASCADE"),
        index=True,
    )
    member_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL", onupdate="CASCADE"),
        index=True,
    )
    request_method: Mapped[str]
    request_path: Mapped[str]
    request_input: Mapped[JsonValue | None] = mapped_column(JSONB)
    response_status: Mapped[int]
    response_content: Mapped[JsonValue | None] = mapped_column(JSONB)
    memory_usage: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
