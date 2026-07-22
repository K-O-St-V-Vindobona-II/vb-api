from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class P4xPartner(Base):
    """member_id/contact_id/p4x_account_id/p4x_specialcontact_id is an
    exclusive-arc polymorphic association: exactly one is set, enforced by
    the CHECK below. Real FKs per target table since Postgres can't point a
    single FK at "whichever table a discriminator column names"."""

    __tablename__ = "p4x_partners"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(member_id, contact_id, p4x_account_id,"
            " p4x_specialcontact_id) = 1",
            name="p4x_partners_partner_exclusive_arc_check",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    iban: Mapped[str | None] = mapped_column(unique=True)
    member_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="RESTRICT", onupdate="CASCADE"),
        index=True,
    )
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT", onupdate="CASCADE"),
        index=True,
    )
    p4x_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("p4x_accounts.id", ondelete="RESTRICT", onupdate="CASCADE"),
        index=True,
    )
    p4x_specialcontact_id: Mapped[int | None] = mapped_column(
        ForeignKey("p4x_specialcontacts.id", ondelete="RESTRICT", onupdate="CASCADE"),
        index=True,
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def partner_type(self) -> str:
        if self.member_id is not None:
            return "member"
        if self.contact_id is not None:
            return "contact"
        if self.p4x_account_id is not None:
            return "account"
        if self.p4x_specialcontact_id is not None:
            return "special"
        msg = "P4xPartner row violates its exclusive-arc CHECK constraint."
        raise ValueError(msg)

    @property
    def partner_id(self) -> int:
        for col in (
            self.member_id,
            self.contact_id,
            self.p4x_account_id,
            self.p4x_specialcontact_id,
        ):
            if col is not None:
                return col
        msg = "P4xPartner row violates its exclusive-arc CHECK constraint."
        raise ValueError(msg)
