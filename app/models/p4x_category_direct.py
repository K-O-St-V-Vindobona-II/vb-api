from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.p4x_category import P4xCategory
    from app.models.p4x_transaction import P4xTransaction


class P4xCategoryDirect(Base):
    """A transaction may only have one active direct assignment per
    category - the partial unique index below closes a race condition
    where two concurrent set-category-direct requests for the same
    transaction/category pair could otherwise both succeed, same pattern
    as StandesdbImage's owner/hash partial index."""

    __tablename__ = "p4x_category_directs"
    __table_args__ = (
        Index(
            "p4x_category_directs_tx_category_active_uniq",
            "p4x_transaction_id",
            "p4x_category_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    p4x_transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("p4x_transactions.id", ondelete="CASCADE", onupdate="CASCADE"),
        index=True,
    )
    p4x_category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("p4x_categories.id", ondelete="RESTRICT", onupdate="CASCADE"),
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    transaction: Mapped[P4xTransaction] = relationship(
        back_populates="category_directs", lazy="select"
    )
    category: Mapped[P4xCategory] = relationship(
        back_populates="category_directs", lazy="joined"
    )
