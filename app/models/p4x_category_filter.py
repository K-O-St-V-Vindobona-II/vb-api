from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.p4x_account import P4xAccount
    from app.models.p4x_category import P4xCategory
    from app.models.p4x_category_filter_hit import P4xCategoryFilterHit


class P4xCategoryFilter(Base):
    __tablename__ = "p4x_category_filters"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    p4x_account_id: Mapped[int] = mapped_column(
        ForeignKey("p4x_accounts.id", ondelete="CASCADE", onupdate="CASCADE"),
        index=True,
    )
    iban: Mapped[str | None]
    min_amount: Mapped[float | None]
    max_amount: Mapped[float | None]
    subject_mode: Mapped[str]
    subject: Mapped[str | None]
    p4x_category_id: Mapped[int] = mapped_column(
        ForeignKey("p4x_categories.id", ondelete="RESTRICT", onupdate="CASCADE"),
        index=True,
    )
    created_at: Mapped[datetime | None]
    updated_at: Mapped[datetime | None]

    account: Mapped[P4xAccount] = relationship(
        back_populates="category_filters", lazy="joined"
    )
    category: Mapped[P4xCategory] = relationship(
        back_populates="category_filters", lazy="joined"
    )
    filter_hits: Mapped[list[P4xCategoryFilterHit]] = relationship(
        back_populates="category_filter", lazy="select"
    )
