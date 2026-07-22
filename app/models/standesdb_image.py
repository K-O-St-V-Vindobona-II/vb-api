from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class StandesdbImage(Base):
    """owner_member_id/owner_contact_id is an exclusive-arc polymorphic
    association: exactly one is set, enforced by the CHECK below. Real FKs
    per target table since Postgres can't point a single FK at "whichever
    table a discriminator column names"."""

    __tablename__ = "standesdb_images"
    __table_args__ = (
        CheckConstraint(
            "size IS NULL OR size >= 0", name="standesdb_images_size_check"
        ),
        CheckConstraint(
            "width IS NULL OR width > 0", name="standesdb_images_width_check"
        ),
        CheckConstraint(
            "height IS NULL OR height > 0", name="standesdb_images_height_check"
        ),
        CheckConstraint(
            "(owner_member_id IS NOT NULL AND owner_contact_id IS NULL) "
            "OR (owner_member_id IS NULL AND owner_contact_id IS NOT NULL)",
            name="standesdb_images_owner_exclusive_arc_check",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    owner_member_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE", onupdate="CASCADE")
    )
    owner_contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE", onupdate="CASCADE")
    )
    extension: Mapped[str | None]
    type: Mapped[str | None]
    size: Mapped[int | None]
    height: Mapped[int | None]
    width: Mapped[int | None]
    sha256_hash: Mapped[str]
    description: Mapped[str | None]
    default: Mapped[int | None] = mapped_column(default=0)
    created_by: Mapped[int | None]
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def owner_type(self) -> str:
        return "member" if self.owner_member_id is not None else "contact"

    @property
    def owner_id(self) -> int:
        if self.owner_member_id is not None:
            return self.owner_member_id
        if self.owner_contact_id is not None:
            return self.owner_contact_id
        msg = "StandesdbImage row violates its exclusive-arc CHECK constraint."
        raise ValueError(msg)
