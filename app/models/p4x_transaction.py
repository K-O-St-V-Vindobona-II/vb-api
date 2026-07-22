from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.types import FlexibleDate

if TYPE_CHECKING:
    from app.models.p4x_account import P4xAccount
    from app.models.p4x_category_direct import P4xCategoryDirect
    from app.models.p4x_category_filter_hit import P4xCategoryFilterHit
    from app.models.p4x_partner import P4xPartner


class P4xTransaction(Base):
    """delegating_member_id/delegating_contact_id/delegating_p4x_account_id/
    delegating_p4x_specialcontact_id is an exclusive-arc polymorphic
    association: at most one is set (the field is optional), enforced by
    the CHECK below."""

    __tablename__ = "p4x_transactions"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(delegating_member_id, delegating_contact_id,"
            " delegating_p4x_account_id, delegating_p4x_specialcontact_id)"
            " <= 1",
            name="p4x_transactions_delegating_partner_arc_check",
        ),
    )

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
    delegating_member_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL", onupdate="CASCADE"),
        index=True,
    )
    delegating_contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL", onupdate="CASCADE"),
        index=True,
    )
    delegating_p4x_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("p4x_accounts.id", ondelete="SET NULL", onupdate="CASCADE"),
        index=True,
    )
    delegating_p4x_specialcontact_id: Mapped[int | None] = mapped_column(
        ForeignKey("p4x_specialcontacts.id", ondelete="SET NULL", onupdate="CASCADE"),
        index=True,
    )
    comment: Mapped[str | None]
    raw: Mapped[str | None] = mapped_column(Text)
    attachment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped[P4xAccount] = relationship(
        back_populates="transactions",
        foreign_keys=[p4x_account_id],
        lazy="joined",
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

    @property
    def delegating_partner_type(self) -> str | None:
        if self.delegating_member_id is not None:
            return "member"
        if self.delegating_contact_id is not None:
            return "contact"
        if self.delegating_p4x_account_id is not None:
            return "account"
        if self.delegating_p4x_specialcontact_id is not None:
            return "special"
        return None

    @property
    def delegating_partner_id(self) -> int | None:
        for col in (
            self.delegating_member_id,
            self.delegating_contact_id,
            self.delegating_p4x_account_id,
            self.delegating_p4x_specialcontact_id,
        ):
            if col is not None:
                return col
        return None
