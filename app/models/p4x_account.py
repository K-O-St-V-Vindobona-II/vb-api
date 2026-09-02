from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, DateTime, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.p4x_category_filter import P4xCategoryFilter
    from app.models.p4x_transaction import P4xTransaction

# The "Girokonto" account (formerly integer id 1) - the sole account that
# tracks SumUp settlements and is shown as the association's main IBAN/BIC
# for donations. Fixed forever - unlike p4x_categories, p4x_accounts has
# no unique business-name field robust enough for a name-based lookup
# (IBAN is unique but represents an operational bank detail, not a stable
# label), so this is a migration-fixed literal instead, analogous to
# public_site_settings.SETTINGS_ROW_ID.
GIROKONTO_ACCOUNT_ID = uuid.UUID("01a05d4d-14b5-7482-a9a3-d9e3e06fa7c6")


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

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    iban: Mapped[str] = mapped_column(unique=True)
    bic: Mapped[str | None]
    label: Mapped[str | None]
    init_date: Mapped[datetime.date | None] = mapped_column(Date)
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
        back_populates="account",
        foreign_keys="P4xTransaction.p4x_account_id",
        lazy="select",
    )
    category_filters: Mapped[list[P4xCategoryFilter]] = relationship(
        back_populates="account", lazy="select"
    )

    @property
    def cn(self) -> str:
        return (self.label or "").strip()
