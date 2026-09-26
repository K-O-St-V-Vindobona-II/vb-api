from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.enums import OauthProvider, enum_values

if TYPE_CHECKING:
    from app.models.member import Member


class MembersOauth2Binding(Base):
    __tablename__ = "members_oauth2bindings"
    __table_args__ = (
        # An external identity belongs to exactly one member ...
        UniqueConstraint(
            "provider",
            "remote_id",
            name="members_oauth2bindings_provider_remote_id_key",
        ),
        # ... and a member has at most one identity per provider. The leading
        # member_id column also serves the lookups by member.
        UniqueConstraint(
            "member_id",
            "provider",
            name="members_oauth2bindings_member_id_provider_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE", onupdate="CASCADE")
    )

    provider: Mapped[OauthProvider] = mapped_column(
        Enum(
            OauthProvider,
            name="oauth_provider",
            native_enum=True,
            values_callable=enum_values,
        )
    )
    remote_id: Mapped[str]
    remote_name: Mapped[str]
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    lastuse_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    member: Mapped[Member] = relationship(back_populates="oauth_bindings")
