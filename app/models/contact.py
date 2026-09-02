from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.enums import ContactType, enum_values

if TYPE_CHECKING:
    from app.models.org import Org
    from app.models.standesdb_image import StandesdbImage


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        CheckConstraint(
            "datum_accuracy IS NULL OR datum_accuracy BETWEEN 0 AND 3",
            name="contacts_datum_accuracy_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid7)
    kontakttyp: Mapped[ContactType] = mapped_column(
        Enum(
            ContactType,
            name="contact_type",
            native_enum=True,
            values_callable=enum_values,
        )
    )
    anrede: Mapped[str | None]
    name: Mapped[str] = mapped_column(String, unique=True)
    couleurname: Mapped[str | None]
    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("orgs.id", ondelete="RESTRICT", onupdate="CASCADE")
    )
    # Postgres-maintained (GENERATED ALWAYS AS ... STORED, see migration
    # 9618c2de197f) - name weighted above couleurname, org_id weighted
    # lowest (lets a query mix a name with an org qualifier in one search
    # string, same reasoning as Member.search_vector). Never written from
    # Python, only read via full-text @@/ts_rank() in
    # standesdb_service.search_members_and_contacts().
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('german', coalesce(name, '')), 'A') || "
            "setweight(to_tsvector('german', coalesce(couleurname, '')), 'B') || "
            "setweight(to_tsvector('german', coalesce(org_id, '')), 'C')",
            persisted=True,
        ),
    )

    adresse_anschrift: Mapped[str | None]
    adresse_plz: Mapped[str | None]
    adresse_ort: Mapped[str | None]
    adresse_land: Mapped[str | None]

    zustellungen: Mapped[bool | None] = mapped_column(default=False)
    email: Mapped[str | None]
    rufnummer: Mapped[str | None]

    datum: Mapped[date | None] = mapped_column(Date)
    datum_accuracy: Mapped[int | None] = mapped_column(default=0)

    anmerkungen: Mapped[str | None] = mapped_column(Text)

    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # References members.id_uuid, not members.id - members itself won't
    # have a UUID primary key until its own Final-Cutover.
    modified_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("members.id_uuid", ondelete="SET NULL", onupdate="CASCADE"),
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    org: Mapped[Org] = relationship(lazy="joined")

    images: Mapped[list[StandesdbImage]] = relationship(
        foreign_keys="StandesdbImage.owner_contact_id",
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
    def default_image(self) -> uuid.UUID | None:
        for img in self.images:
            if img.default and not img.deleted_at:
                return img.id
        for img in self.images:
            if not img.deleted_at:
                return img.id
        return None
