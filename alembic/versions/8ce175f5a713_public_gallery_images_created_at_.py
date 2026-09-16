"""public gallery images created_at updated_at server default

Revision ID: 8ce175f5a713
Revises: 830b5a670c5b
Create Date: 2026-09-16 17:16:47.594775

public_gallery_images has had created_at/updated_at columns and the
shared set_updated_at() trigger (wired up in 74d19e4af679_updated_at_
trigger.py) since Migration Wave 2, but unlike every other audited table
it never got a server_default=now() on either column - the only table
in the schema where every INSERT still depended on the application
supplying both timestamps explicitly. Closing that gap the same way
40cd764a92e7_add_missing_audit_columns.py did for its 8 tables; no data
backfill needed here since the columns already existed and are already
populated on every row.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8ce175f5a713"
down_revision: str | Sequence[str] | None = "830b5a670c5b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.alter_column(
        "public_gallery_images", "created_at", server_default=sa.text("now()")
    )
    op.alter_column(
        "public_gallery_images", "updated_at", server_default=sa.text("now()")
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.alter_column("public_gallery_images", "created_at", server_default=None)
    op.alter_column("public_gallery_images", "updated_at", server_default=None)
