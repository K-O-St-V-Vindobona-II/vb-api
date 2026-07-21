from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PersonalAccessToken(Base):
    __tablename__ = "personal_access_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    tokenable_type: Mapped[str]  # 'member'
    tokenable_id: Mapped[int]  # ID of member
    name: Mapped[str]  # e.g. 'vue-spa-login'
    token: Mapped[str] = mapped_column(unique=True, index=True)  # JWT-ID (jti)
    refresh_token_hash: Mapped[str | None]
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
