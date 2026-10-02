import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE", onupdate="CASCADE"),
        index=True,
    )
    jti: Mapped[str] = mapped_column(unique=True, index=True)  # JWT-ID claim
    refresh_token_hash: Mapped[str | None]
    # Both lifetime checks (idle timeout, absolute lifetime) compare against
    # these columns and skip a comparison when the value is missing, so they
    # must never be NULL: a session row without them would never expire.
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
