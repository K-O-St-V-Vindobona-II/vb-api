from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class P4xPartner(Base):
    __tablename__ = "p4x_partners"

    id: Mapped[int] = mapped_column(primary_key=True)
    iban: Mapped[str | None] = mapped_column(unique=True)
    partner_type: Mapped[str]
    partner_id: Mapped[int] = mapped_column(index=True)
    created_at: Mapped[datetime | None]
    updated_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]
