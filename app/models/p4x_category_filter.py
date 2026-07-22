from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Numeric
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
    name: Mapped[str] = mapped_column(unique=True)
    p4x_account_id: Mapped[int] = mapped_column(
        ForeignKey("p4x_accounts.id", ondelete="CASCADE", onupdate="CASCADE"),
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
    p4x_category_id: Mapped[int] = mapped_column(
        ForeignKey("p4x_categories.id", ondelete="RESTRICT", onupdate="CASCADE"),
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
