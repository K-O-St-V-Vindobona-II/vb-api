"""bound client_user_agents.string to 512 characters

Revision ID: 5a1c7e93b2d4
Revises: 8ce175f5a713
Create Date: 2026-09-21 09:10:00.000000

The User-Agent header is chosen by the client and was stored unbounded. A
random value of about 3 KB exceeds the btree row limit of the unique
constraint on `string`, so the insert failed and the request was left out of
the activity log; shorter values still grew the table by one row per request.

The constraint is added NOT VALID (enforced for every new or updated row) and
validated right away when no stored value is longer than the bound. If old
rows exceed it, the constraint stays unvalidated; shorten or remove them and
run `ALTER TABLE client_user_agents VALIDATE CONSTRAINT
client_user_agents_string_check` afterwards.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5a1c7e93b2d4"
down_revision: str | Sequence[str] | None = "8ce175f5a713"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "client_user_agents_string_check"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE client_user_agents ADD CONSTRAINT client_user_agents_string_check "
        "CHECK (char_length(string) <= 512) NOT VALID"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM client_user_agents WHERE char_length(string) > 512
            ) THEN
                ALTER TABLE client_user_agents
                    VALIDATE CONSTRAINT client_user_agents_string_check;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint(_CONSTRAINT, "client_user_agents", type_="check")
