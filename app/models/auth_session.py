import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    # References members.id_uuid, not members.id - members itself won't
    # have a UUID primary key until its own Final-Cutover (slice 32).
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("members.id_uuid", ondelete="CASCADE", onupdate="CASCADE")
    )
    jti: Mapped[str] = mapped_column(unique=True, index=True)  # JWT-ID claim
    refresh_token_hash: Mapped[str | None]
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
