from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.types import FlexibleDate

if TYPE_CHECKING:
    from app.models.p4x_category_filter import P4xCategoryFilter
    from app.models.p4x_transaction import P4xTransaction


class P4xAccount(Base):
    __tablename__ = "p4x_accounts"
    __table_args__ = (
        CheckConstraint(
            "iban ~ '^[A-Z]{2}[0-9]{2}[A-Z0-9 ]{4,}$'", name="p4x_accounts_iban_check"
        ),
        CheckConstraint(
            "bic IS NULL OR bic ~ '^[A-Za-z0-9]{1,11}$'",
            name="p4x_accounts_bic_check",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    iban: Mapped[str] = mapped_column(unique=True)
    bic: Mapped[str | None]
    label: Mapped[str | None]
    init_date: Mapped[datetime.date | None] = mapped_column(FlexibleDate)
    init_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    transactions: Mapped[list[P4xTransaction]] = relationship(
        back_populates="account", lazy="select"
    )
    category_filters: Mapped[list[P4xCategoryFilter]] = relationship(
        back_populates="account", lazy="select"
    )

    @property
    def cn(self) -> str:
        return (self.label or "").strip()
