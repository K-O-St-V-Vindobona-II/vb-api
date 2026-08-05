from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PublicSiteProgrammHint(Base):
    """One bullet in the "Hinweise" list under the public Programm section.

    Rendered with a checkmark icon that is purely CSS/template-driven on
    the vb-www side (see ProgrammSection.vue) - only the text itself lives
    here, never the icon markup.
    """

    __tablename__ = "public_site_programm_hints"
    __table_args__ = (
        CheckConstraint(
            "char_length(text) > 0", name="public_site_programm_hints_text_check"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Attribute named `content`, not `text` - the DB column is "text" (see
    # migration), but that name collides with the sqlalchemy.text() import
    # needed below for server_default: as a class attribute it would
    # shadow the imported function within this class body for every line
    # after it.
    content: Mapped[str] = mapped_column("text", Text)
    sort_order: Mapped[int] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
