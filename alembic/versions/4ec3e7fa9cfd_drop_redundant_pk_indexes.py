"""drop redundant pk indexes

Revision ID: 4ec3e7fa9cfd
Revises: a6af1c272f2c
Create Date: 2026-09-02 21:30:00.000000

password_reset_tokens.email and request_logs.id each carried a second,
plain index (`index=True` in the model) alongside the unique index a
primary key already creates - a redundant index provides no query
benefit (the PK's own index already serves lookups on that column) and
only costs disk space and write overhead. Same class of finding as
sent_emails' ix_sent_emails_id, dropped in 22fc473b0891 - unrelated to
this table pair but the identical pattern, unrelated to the Integer-PK
-> UUID migration (neither column was ever migrated: email stays a
string PK, request_logs.id stays an int PK by design).

Deliberately not recreated on downgrade, same reasoning as
22fc473b0891: the index was never load-bearing, so a round trip should
not resurrect it.
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4ec3e7fa9cfd"
down_revision: str | None = "a6af1c272f2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DROP INDEX IF EXISTS ix_password_reset_tokens_email")
    op.execute("DROP INDEX IF EXISTS ix_request_logs_id")


def downgrade() -> None:
    """Downgrade schema.

    Both indexes were never load-bearing (the primary key's own index
    already serves every lookup they could), so - same precedent as
    22fc473b0891's ix_sent_emails_id - they are intentionally not
    recreated here.
    """
