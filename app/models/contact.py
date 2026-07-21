from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.types import FlexibleDate

if TYPE_CHECKING:
    from app.models.org import Org
    from app.models.standesdb_image import StandesdbImage


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    kontakttyp: Mapped[str] = mapped_column(String)
    anrede: Mapped[str | None]
    name: Mapped[str] = mapped_column(String, unique=True)
    couleurname: Mapped[str | None]
    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("orgs.id", ondelete="RESTRICT", onupdate="CASCADE")
    )

    adresse_anschrift: Mapped[str | None]
    adresse_plz: Mapped[str | None]
    adresse_ort: Mapped[str | None]
    adresse_land: Mapped[str | None]

    zustellungen: Mapped[bool | None] = mapped_column(default=False)
    email: Mapped[str | None]
    rufnummer: Mapped[str | None]

    datum: Mapped[date | None] = mapped_column(FlexibleDate)
    datum_accuracy: Mapped[int | None] = mapped_column(default=0)

    anmerkungen: Mapped[str | None] = mapped_column(Text)

    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_by: Mapped[int | None]
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    org: Mapped[Org] = relationship(lazy="joined")

    images: Mapped[list[StandesdbImage]] = relationship(
        primaryjoin=(
            "and_(Contact.id == foreign(StandesdbImage"
            ".owner_id), StandesdbImage.owner_type "
            "== 'contact')"
        ),
        viewonly=True,
        lazy="select",
    )

    @property
    def cn(self) -> str:
        name = self.name or ""
        if name and self.couleurname:
            name = f"{name} v/o {self.couleurname}"
        elif self.couleurname:
            name = self.couleurname
        return " ".join(name.split())

    @property
    def default_image(self) -> int | None:
        for img in self.images:
            if img.default and not img.deleted_at:
                return img.id
        for img in self.images:
            if not img.deleted_at:
                return img.id
        return None
