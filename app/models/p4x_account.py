from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.types import FlexibleDate

if TYPE_CHECKING:
    from app.models.p4x_category_filter import P4xCategoryFilter
    from app.models.p4x_transaction import P4xTransaction


class P4xAccount(Base):
    __tablename__ = "p4x_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    iban: Mapped[str] = mapped_column(unique=True)
    bic: Mapped[str | None]
    label: Mapped[str | None]
    init_date: Mapped[datetime.date | None] = mapped_column(FlexibleDate)
    init_balance: Mapped[float] = mapped_column(default=0)
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
