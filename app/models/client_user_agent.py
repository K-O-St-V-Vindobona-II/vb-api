import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# The User-Agent header is chosen by the client. Real browser strings stay
# well below this; the bound keeps every row far below the btree index row
# limit (about 2.7 KB) that the unique constraint on `string` runs into.
MAX_USER_AGENT_LENGTH = 512


class ClientUserAgent(Base):
    __tablename__ = "client_user_agents"
    __table_args__ = (
        CheckConstraint(
            f"char_length(string) <= {MAX_USER_AGENT_LENGTH}",
            name="client_user_agents_string_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    string: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
