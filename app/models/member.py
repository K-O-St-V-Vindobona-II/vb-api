from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.types import FlexibleDate

if TYPE_CHECKING:
    from app.models.member_badge import MemberBadge
    from app.models.member_key import MemberKey
    from app.models.member_role import MemberRole
    from app.models.members_oauth2binding import MembersOauth2Binding
    from app.models.org import Org
    from app.models.standesdb_image import StandesdbImage
    from app.models.state import State


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # --- Name ---
    vortitel: Mapped[str | None]
    vorname: Mapped[str | None]
    nachname: Mapped[str | None]
    nachname_geburt: Mapped[str | None]
    nachtitel: Mapped[str | None]
    couleurname: Mapped[str | None]

    # --- Organization & Status ---
    org_id: Mapped[str | None] = mapped_column(ForeignKey("orgs.id"))
    state_id: Mapped[str | None] = mapped_column(ForeignKey("states.id"))
    gruender: Mapped[bool | None] = mapped_column(default=False)
    entlassen: Mapped[bool | None] = mapped_column(default=False)
    verstorben: Mapped[bool | None] = mapped_column(default=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), default=0)

    # --- Fuzzy Dates ---
    geburtsdatum: Mapped[date | None] = mapped_column(FlexibleDate)
    geburtsdatum_accuracy: Mapped[int | None] = mapped_column(default=0)
    aufnahmedatum: Mapped[date | None] = mapped_column(FlexibleDate)
    aufnahmedatum_accuracy: Mapped[int | None] = mapped_column(default=0)
    branderdatum: Mapped[date | None] = mapped_column(FlexibleDate)
    branderdatum_accuracy: Mapped[int | None] = mapped_column(default=0)
    burschungsdatum: Mapped[date | None] = mapped_column(FlexibleDate)
    burschungsdatum_accuracy: Mapped[int | None] = mapped_column(default=0)
    philistrierungsdatum: Mapped[date | None] = mapped_column(FlexibleDate)
    philistrierungsdatum_accuracy: Mapped[int | None] = mapped_column(default=0)
    entlassungsdatum: Mapped[date | None] = mapped_column(FlexibleDate)
    entlassungsdatum_accuracy: Mapped[int | None] = mapped_column(default=0)
    sterbedatum: Mapped[date | None] = mapped_column(FlexibleDate)
    sterbedatum_accuracy: Mapped[int | None] = mapped_column(default=0)

    # --- Contact ---
    email: Mapped[str | None] = mapped_column(unique=True, index=True)
    email_verified_at: Mapped[datetime | None]
    url: Mapped[str | None]
    mkv_ogv_url: Mapped[str | None]
    rufnummer_mobil: Mapped[str | None]
    rufnummer_privat: Mapped[str | None]
    rufnummer_beruf: Mapped[str | None]

    # --- Delivery & Addresses ---
    zustellungen: Mapped[str | None] = mapped_column(default="deaktiviert")
    adresse_privat_anschrift: Mapped[str | None]
    adresse_privat_plz: Mapped[str | None]
    adresse_privat_ort: Mapped[str | None]
    adresse_privat_land: Mapped[str | None]
    adresse_beruf_anschrift: Mapped[str | None]
    adresse_beruf_plz: Mapped[str | None]
    adresse_beruf_ort: Mapped[str | None]
    adresse_beruf_land: Mapped[str | None]

    # --- Employment ---
    arbeitgeber: Mapped[str | None]
    taetigkeit: Mapped[str | None]

    # --- Miscellaneous ---
    mitgliedschaften: Mapped[str | None] = mapped_column(Text)
    verbandchargen: Mapped[str | None] = mapped_column(Text)
    anmerkungen: Mapped[str | None] = mapped_column(Text)
    grabadresse: Mapped[str | None]

    # --- Preferences ---
    chroniclemail: Mapped[bool | None] = mapped_column(default=False)

    # --- P4x (Financial System) ---
    p4x_init_date: Mapped[date | None] = mapped_column(FlexibleDate)
    p4x_init_balance: Mapped[int | None]
    p4x_freed: Mapped[bool | None]
    p4x_comment: Mapped[str | None]

    # --- Auth ---
    auth_password: Mapped[str | None]
    auth_lastlogin_provider: Mapped[str | None]
    auth_lastlogin: Mapped[datetime | None]
    auth_lastsignal: Mapped[datetime | None]
    auth_lastlogout: Mapped[datetime | None]
    auth_locked: Mapped[bool | None] = mapped_column(default=True)

    # --- Audit ---
    modified_at: Mapped[datetime | None]
    modified_by: Mapped[int | None]
    created_at: Mapped[datetime | None]
    updated_at: Mapped[datetime | None]

    # --- Relationships ---
    org: Mapped[Org] = relationship(lazy="joined")
    state: Mapped[State] = relationship(lazy="joined")

    parent: Mapped[Member] = relationship(
        remote_side="[Member.id]",
        foreign_keys="[Member.parent_id]",
        lazy="select",
    )
    children: Mapped[list[Member]] = relationship(
        foreign_keys="[Member.parent_id]",
        lazy="select",
        overlaps="parent",
    )

    member_roles: Mapped[list[MemberRole]] = relationship(
        lazy="joined",
        back_populates="member",
    )
    member_badges: Mapped[list[MemberBadge]] = relationship(lazy="select")
    member_keys: Mapped[list[MemberKey]] = relationship(lazy="select")

    oauth_bindings: Mapped[list[MembersOauth2Binding]] = relationship(
        lazy="select",
        back_populates="member",
    )

    images: Mapped[list[StandesdbImage]] = relationship(
        primaryjoin=(
            "and_(Member.id == foreign(StandesdbImage"
            ".owner_id), StandesdbImage.owner_type "
            "== 'member')"
        ),
        viewonly=True,
        lazy="select",
    )

    @property
    def cn(self) -> str:
        name = f"{self.vorname or ''} {self.nachname or ''}"
        name = name.strip()
        if name and self.couleurname:
            prefix = "wl." if (self.verstorben or self.entlassen) else "v/o"
            name = f"{name} {prefix} {self.couleurname}"
        elif self.couleurname:
            name = self.couleurname
        return " ".join(name.split())

    @property
    def cn_full(self) -> str:
        parts = [
            self.vortitel or "",
            self.vorname or "",
            self.nachname or "",
            self.nachtitel or "",
        ]
        name = " ".join(p for p in parts if p).strip()
        if name and self.couleurname:
            prefix = "wl." if (self.verstorben or self.entlassen) else "v/o"
            name = f"{name} {prefix} {self.couleurname}"
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

    @property
    def google_linked(self) -> bool:
        return any(b.provider == "google" for b in self.oauth_bindings)
