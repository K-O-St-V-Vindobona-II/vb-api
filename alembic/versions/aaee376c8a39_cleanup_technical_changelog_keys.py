"""cleanup technical changelog keys

Revision ID: aaee376c8a39
Revises: 20db5b81733c
Create Date: 2026-07-25 10:30:12.835049

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "aaee376c8a39"
down_revision: str | Sequence[str] | None = "20db5b81733c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors app.services.standesdb_service._CHANGELOG_EXCLUDED_KEYS — kept as a
# literal tuple here (not imported) since migrations must stay runnable even
# after that application constant changes or is removed later. created_at/
# deleted_at are deliberately NOT included, see that constant's docstring.
_EXCLUDED_KEYS = (
    "auth_lastlogin",
    "auth_lastlogin_provider",
    "auth_lastsignal",
    "auth_lastlogout",
    "modified_at",
    "modified_by",
    "updated_at",
)


def upgrade() -> None:
    """Remove pre-existing changelog rows for technical/audit columns.

    These rows predate the read/write filter added in this same change and
    are pure noise in the Änderungshistorie (e.g. auth_lastsignal bumped on
    every request via a raw bulk UPDATE that bypassed the diff mechanism).
    """
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM members_logs WHERE key = ANY(:keys)"),
        {"keys": list(_EXCLUDED_KEYS)},
    )
    conn.execute(
        sa.text("DELETE FROM contacts_logs WHERE key = ANY(:keys)"),
        {"keys": list(_EXCLUDED_KEYS)},
    )


def downgrade() -> None:
    """Irreversible: deleted changelog rows cannot be restored."""
