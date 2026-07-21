from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class SentEmail(Base):
    __tablename__ = "sent_emails"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    mail_from: Mapped[str | None] = mapped_column("from")
    to: Mapped[str | None]
    cc: Mapped[str | None]
    bcc: Mapped[str | None]
    subject: Mapped[str | None]
    body: Mapped[str | None]
    headers: Mapped[str | None]
    attachments: Mapped[str | None]
    mailer: Mapped[str | None]
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
