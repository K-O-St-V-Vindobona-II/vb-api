import uuid

from sqlalchemy import CheckConstraint, Enum, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models.enums import BadgeGroup, enum_values


class Badge(Base):
    __tablename__ = "badges"
    __table_args__ = (
        CheckConstraint('"order" IS NULL OR "order" >= 0', name="badges_order_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Additive prep column for the schema-wide UUID-PK migration (see
    # e2f6d45fab87_badges_and_keys_id_uuid_phase_a.py) - not yet the
    # primary key. badges_members cuts over onto this in slice 14.
    id_uuid: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid7)
    name: Mapped[str | None]
    group: Mapped[BadgeGroup | None] = mapped_column(
        Enum(
            BadgeGroup,
            name="badge_group",
            native_enum=True,
            values_callable=enum_values,
        )
    )
    order: Mapped[int | None] = mapped_column(default=0)
