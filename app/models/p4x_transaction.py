from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.types import FlexibleDate

if TYPE_CHECKING:
    from app.models.p4x_account import P4xAccount
    from app.models.p4x_category_direct import P4xCategoryDirect
    from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
    from app.models.p4x_partner import P4xPartner


class P4xTransaction(Base):
    __tablename__ = "p4x_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256hash: Mapped[str] = mapped_column(String, unique=True)
    booking: Mapped[date] = mapped_column(FlexibleDate, index=True)
    valuation: Mapped[date] = mapped_column(FlexibleDate, index=True)
    iban: Mapped[str] = mapped_column(String, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), index=True)
    subject: Mapped[str] = mapped_column(String, index=True)
    p4x_account_id: Mapped[int] = mapped_column(
        ForeignKey("p4x_accounts.id", ondelete="RESTRICT", onupdate="CASCADE"),
        index=True,
    )
    delegating_partner_type: Mapped[str | None] = mapped_column(index=True)
    delegating_partner_id: Mapped[int | None] = mapped_column(index=True)
    comment: Mapped[str | None]
    raw: Mapped[str | None] = mapped_column(Text)
    attachment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped[P4xAccount] = relationship(
        back_populates="transactions", lazy="joined"
    )
    category_directs: Mapped[list[P4xCategoryDirect]] = relationship(
        back_populates="transaction", lazy="select"
    )
    category_filter_hits: Mapped[list[P4xCategoryFilterHit]] = relationship(
        back_populates="transaction", lazy="select"
    )
    partner: Mapped[P4xPartner | None] = relationship(
        primaryjoin="P4xTransaction.iban == foreign(P4xPartner.iban)",
        uselist=False,
        viewonly=True,
        lazy="joined",
    )

    @property
    def has_attachment(self) -> bool:
        return bool(self.attachment)
