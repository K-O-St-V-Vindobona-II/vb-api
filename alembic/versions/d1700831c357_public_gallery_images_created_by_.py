"""public gallery images created by onupdate cascade

Revision ID: d1700831c357
Revises: d2d135fcfb3f
Create Date: 2026-09-11 23:44:46.320659

public_gallery_images_created_by_fkey was the schema's one remaining FK
without ON UPDATE CASCADE — every other one of the 51 FK constraints in
the schema has it. Defined without it back in Slice 12 of the
integer-PK -> UUID migration; e097221b6c5f_members_final_cutover.py later
found and documented the gap but deliberately left it as-is rather than
"silently tightening" it as a side effect of an unrelated PK-type
migration. This is that deliberate follow-up.

In practice inconsequential either way (UUID primary keys never change
value), but this closes the last inconsistency against the schema's own
convention.
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1700831c357"
down_revision: str | None = "d2d135fcfb3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        "public_gallery_images_created_by_fkey",
        "public_gallery_images",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "public_gallery_images_created_by_fkey",
        "public_gallery_images",
        "members",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "public_gallery_images_created_by_fkey",
        "public_gallery_images",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "public_gallery_images_created_by_fkey",
        "public_gallery_images",
        "members",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
    )
