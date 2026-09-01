import uuid

from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class P4xSpecialcontact(Base):
    __tablename__ = "p4x_special_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Additive prep column for the schema-wide UUID-PK migration (see
    # a67f0d2a4c5e_p4x_categories_and_p4x_special_contacts_.py) - not yet
    # the primary key. p4x_partners/p4x_transactions cut over onto this
    # in their own slices.
    id_uuid: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid7)
    cn: Mapped[str | None]

    @property
    def search_label(self) -> str:
        return f"Spezial: {self.cn or ''}"
