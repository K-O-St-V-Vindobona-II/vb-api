"""${message}

Revision ID: ${up_revision}
Revises:${" " + comma(down_revision) if down_revision else ""}
Create Date: ${create_date}

"""

from typing import Sequence
% if "sa." in ((upgrades or "") + (downgrades or "")) or imports:

% if "sa." in ((upgrades or "") + (downgrades or "")):
import sqlalchemy as sa
% endif
% if imports:
${imports}
% endif
% endif

from alembic import op

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision).replace("'", '"')}
down_revision: str | Sequence[str] | None = ${repr(down_revision).replace("'", '"')}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels).replace("'", '"')}
depends_on: str | Sequence[str] | None = ${repr(depends_on).replace("'", '"')}


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
% if upgrades:
    ${upgrades}
% endif


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
% if downgrades:
    ${downgrades}
% endif
