import uuid

from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Key(Base):
    __tablename__ = "keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Additive prep column for the schema-wide UUID-PK migration (see
    # e2f6d45fab87_badges_and_keys_id_uuid_phase_a.py) - not yet the
    # primary key. keys_members cuts over onto this in slice 14.
    id_uuid: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid7)
    name: Mapped[str | None]
