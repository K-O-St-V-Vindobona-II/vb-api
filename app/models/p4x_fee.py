import datetime

from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models.types import FlexibleDate


class P4xFee(Base):
    __tablename__ = "p4x_fees"

    start: Mapped[datetime.date] = mapped_column(FlexibleDate, primary_key=True)
    fee: Mapped[float]
    protected: Mapped[bool] = mapped_column(default=False)
