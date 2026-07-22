from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.p4x_category_direct import P4xCategoryDirect
    from app.models.p4x_category_filter import P4xCategoryFilter

_HEX_COLOR_CHECK = "'^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$'"


class P4xCategory(Base):
    __tablename__ = "p4x_categories"
    __table_args__ = (
        CheckConstraint(
            f"background_color ~ {_HEX_COLOR_CHECK}",
            name="p4x_categories_background_color_check",
        ),
        CheckConstraint(
            f"text_color ~ {_HEX_COLOR_CHECK}",
            name="p4x_categories_text_color_check",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    label: Mapped[str]
    background_color: Mapped[str]
    text_color: Mapped[str]
    protected: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    category_filters: Mapped[list[P4xCategoryFilter]] = relationship(
        back_populates="category", lazy="select"
    )
    category_directs: Mapped[list[P4xCategoryDirect]] = relationship(
        back_populates="category", lazy="select"
    )
