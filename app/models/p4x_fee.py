import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, Numeric, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class P4xFee(Base):
    __tablename__ = "p4x_fees"
    __table_args__ = (
        CheckConstraint("fee >= 0", name="p4x_fees_fee_check"),
        # A fee applies from the first day of a month: the balance functions
        # look a fee up by the first day of each month.
        CheckConstraint(
            "extract(day FROM start) = 1", name="p4x_fees_start_first_of_month_check"
        ),
    )

    start: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    fee: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    protected: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
