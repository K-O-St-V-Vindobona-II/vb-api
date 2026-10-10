import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class SentEmail(Base):
    __tablename__ = "sent_emails"
    # The mail log is listed newest first, filtered by month and purged by age.
    __table_args__ = (Index("ix_sent_emails_created_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    mail_from: Mapped[str | None] = mapped_column("from")
    to: Mapped[str | None]
    cc: Mapped[str | None]
    bcc: Mapped[str | None]
    subject: Mapped[str | None]
    body: Mapped[str | None]
    headers: Mapped[str | None]
    attachments: Mapped[str | None]
    mailer: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
