from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.p4x_category_filter import P4xCategoryFilter
    from app.models.p4x_transaction import P4xTransaction


class P4xCategoryFilterHit(Base):
    __tablename__ = "p4x_category_filter_hits"
    __table_args__ = (
        UniqueConstraint(
            "p4x_transaction_id",
            "p4x_category_filter_id",
            name="transaction_category_filter_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    p4x_transaction_id: Mapped[int] = mapped_column(
        ForeignKey("p4x_transactions.id"), index=True
    )
    p4x_category_filter_id: Mapped[int] = mapped_column(
        ForeignKey("p4x_category_filters.id"), index=True
    )
    created_at: Mapped[datetime | None]
    updated_at: Mapped[datetime | None]

    transaction: Mapped[P4xTransaction] = relationship(
        back_populates="category_filter_hits", lazy="select"
    )
    category_filter: Mapped[P4xCategoryFilter] = relationship(
        back_populates="filter_hits", lazy="joined"
    )
