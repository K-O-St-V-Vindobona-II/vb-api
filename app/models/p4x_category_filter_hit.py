from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, Uuid
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

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    # References p4x_transactions.id_uuid, not p4x_transactions.id -
    # p4x_transactions itself won't have a UUID primary key until its own
    # Final-Cutover.
    p4x_transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("p4x_transactions.id_uuid", ondelete="CASCADE", onupdate="CASCADE"),
        index=True,
    )
    # References p4x_category_filters.id_uuid, not p4x_category_filters.id
    # - p4x_category_filters itself won't have a UUID primary key until
    # its own Final-Cutover.
    p4x_category_filter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "p4x_category_filters.id_uuid", ondelete="CASCADE", onupdate="CASCADE"
        ),
        index=True,
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    transaction: Mapped[P4xTransaction] = relationship(
        back_populates="category_filter_hits", lazy="select"
    )
    category_filter: Mapped[P4xCategoryFilter] = relationship(
        back_populates="filter_hits", lazy="joined"
    )
