import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class P4xFee(Base):
    __tablename__ = "p4x_fees"
    __table_args__ = (CheckConstraint("fee >= 0", name="p4x_fees_fee_check"),)

    start: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    fee: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    protected: Mapped[bool] = mapped_column(default=False)
