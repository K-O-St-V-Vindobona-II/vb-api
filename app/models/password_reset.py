from datetime import UTC, datetime

from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    email: Mapped[str] = mapped_column(primary_key=True, index=True)
    token: Mapped[str]

    # We use a lambda to ensure the timezone is always attached
    created_at: Mapped[datetime | None] = mapped_column(
        default=lambda: datetime.now(UTC)
    )
