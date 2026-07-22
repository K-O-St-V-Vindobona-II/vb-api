import datetime

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models.types import FlexibleDate


class P4xFee(Base):
    __tablename__ = "p4x_fees"
    __table_args__ = (CheckConstraint("fee >= 0", name="p4x_fees_fee_check"),)

    start: Mapped[datetime.date] = mapped_column(FlexibleDate, primary_key=True)
    fee: Mapped[float]
    protected: Mapped[bool] = mapped_column(default=False)
