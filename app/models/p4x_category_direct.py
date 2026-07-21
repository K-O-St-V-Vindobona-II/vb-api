from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.p4x_category import P4xCategory
    from app.models.p4x_transaction import P4xTransaction


class P4xCategoryDirect(Base):
    __tablename__ = "p4x_category_directs"

    id: Mapped[int] = mapped_column(primary_key=True)
    p4x_transaction_id: Mapped[int] = mapped_column(
        ForeignKey("p4x_transactions.id", ondelete="CASCADE", onupdate="CASCADE"),
        index=True,
    )
    p4x_category_id: Mapped[int] = mapped_column(
        ForeignKey("p4x_categories.id", ondelete="RESTRICT", onupdate="CASCADE"),
        index=True,
    )
    amount: Mapped[float]
    deleted_at: Mapped[datetime | None]

    transaction: Mapped[P4xTransaction] = relationship(
        back_populates="category_directs", lazy="select"
    )
    category: Mapped[P4xCategory] = relationship(
        back_populates="category_directs", lazy="joined"
    )
