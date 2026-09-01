from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.enums import SubjectMode, enum_values

if TYPE_CHECKING:
    from app.models.p4x_account import P4xAccount
    from app.models.p4x_category import P4xCategory
    from app.models.p4x_category_filter_hit import P4xCategoryFilterHit


class P4xCategoryFilter(Base):
    __tablename__ = "p4x_category_filters"
    __table_args__ = (
        CheckConstraint(
            "min_amount IS NULL OR max_amount IS NULL OR min_amount <= max_amount",
            name="p4x_category_filters_min_max_amount_check",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Additive prep column for the schema-wide UUID-PK migration (see
    # d6443ece80ad_p4x_category_filters_id_uuid_and_fk_.py) - not yet the
    # primary key. p4x_category_filter_hits cuts over onto this in its
    # own slice; this table's own Final-Cutover is slice 30.
    id_uuid: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid7)
    name: Mapped[str] = mapped_column(unique=True)
    # References p4x_accounts.id_uuid, not p4x_accounts.id -
    # p4x_accounts itself won't have a UUID primary key until its own
    # Final-Cutover (slice 27).
    p4x_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("p4x_accounts.id_uuid", ondelete="CASCADE", onupdate="CASCADE"),
        index=True,
    )
    iban: Mapped[str | None]
    min_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    max_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    subject_mode: Mapped[SubjectMode] = mapped_column(
        Enum(
            SubjectMode,
            name="p4x_filter_subject_mode",
            native_enum=True,
            values_callable=enum_values,
        )
    )
    subject: Mapped[str | None]
    # References p4x_categories.id_uuid, not p4x_categories.id -
    # p4x_categories itself won't have a UUID primary key until its own
    # Final-Cutover (slice 26).
    p4x_category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("p4x_categories.id_uuid", ondelete="RESTRICT", onupdate="CASCADE"),
        index=True,
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped[P4xAccount] = relationship(
        back_populates="category_filters", lazy="joined"
    )
    category: Mapped[P4xCategory] = relationship(
        back_populates="category_filters", lazy="joined"
    )
    filter_hits: Mapped[list[P4xCategoryFilterHit]] = relationship(
        back_populates="category_filter", lazy="select"
    )
