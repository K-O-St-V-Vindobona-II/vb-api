"""contacts final cutover

Revision ID: 3f82fd850390
Revises: bfe999a46f89
Create Date: 2026-09-02 16:00:00.000000

Three steps in fixed order:

1. contacts_logs.contact_id Referrer-Cutover: a genuine existing FK
   (contacts_logs_contact_id_fkey, still pointing at the still-integer
   contacts.id) cut over to contacts.id_uuid, same ON DELETE/ON UPDATE
   strategy - the one referrer of contacts left standing after Wave C,
   never covered by any earlier slice (contacts_logs.modified_by, a
   genuinely different column, was handled in 267386fa75f3).
2. contacts Final-Cutover: own PK moves from the additive id_uuid to id.
   All four referrers (contacts_logs.contact_id as of step 1,
   p4x_partners.contact_id, p4x_transactions.delegating_contact_id,
   standesdb_images.owner_contact_id) already point at id_uuid, so a
   PostgreSQL RENAME COLUMN updates the column their existing foreign
   keys point to automatically - none of those referrer tables need
   touching here.
3. contacts.modified_by: a bare integer column with no ForeignKey() at
   all (like contacts_logs/members_logs.modified_by before 267386fa75f3)
   gets a real FK to members.id_uuid. Same 0-as-"no member" legacy
   sentinel (40/44 rows), converted to NULL first for the same reason.

downgrade() reverses step 3, then step 2 (dropping and recreating every
referrer's FK around the PK revert, since Postgres refuses to drop a pkey
index a live FK still depends on), then step 1.
"""

from typing import NamedTuple, Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f82fd850390"
down_revision: str | Sequence[str] | None = "bfe999a46f89"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "contacts"
SEQUENCE_NAME = "contacts_id_seq"


class _ReferrerFk(NamedTuple):
    name: str
    child_table: str
    child_column: str
    ondelete: str
    onupdate: str


REFERRERS: list[_ReferrerFk] = [
    _ReferrerFk(
        "contacts_logs_contact_id_fkey",
        "contacts_logs",
        "contact_id",
        "SET NULL",
        "CASCADE",
    ),
    _ReferrerFk(
        "p4x_partners_contact_id_fkey",
        "p4x_partners",
        "contact_id",
        "RESTRICT",
        "CASCADE",
    ),
    _ReferrerFk(
        "p4x_transactions_delegating_contact_id_fkey",
        "p4x_transactions",
        "delegating_contact_id",
        "SET NULL",
        "CASCADE",
    ),
    _ReferrerFk(
        "standesdb_images_owner_contact_id_fkey",
        "standesdb_images",
        "owner_contact_id",
        "CASCADE",
        "CASCADE",
    ),
]

MODIFIED_BY_FK = "contacts_modified_by_fkey"


def _cutover_contacts_logs_contact_id() -> None:
    op.add_column(
        "contacts_logs", sa.Column("contact_id_uuid", sa.Uuid(), nullable=True)
    )
    op.execute(
        "UPDATE contacts_logs cl SET contact_id_uuid = c.id_uuid "
        "FROM contacts c WHERE cl.contact_id = c.id"
    )
    op.drop_constraint(
        "contacts_logs_contact_id_fkey", "contacts_logs", type_="foreignkey"
    )
    op.drop_column("contacts_logs", "contact_id")
    op.alter_column("contacts_logs", "contact_id_uuid", new_column_name="contact_id")
    op.create_foreign_key(
        "contacts_logs_contact_id_fkey",
        "contacts_logs",
        "contacts",
        ["contact_id"],
        ["id_uuid"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def _revert_contacts_logs_contact_id() -> None:
    op.add_column(
        "contacts_logs", sa.Column("contact_id_int", sa.Integer(), nullable=True)
    )
    op.execute(
        "UPDATE contacts_logs cl SET contact_id_int = c.id "
        "FROM contacts c WHERE cl.contact_id = c.id_uuid"
    )
    op.drop_constraint(
        "contacts_logs_contact_id_fkey", "contacts_logs", type_="foreignkey"
    )
    op.drop_column("contacts_logs", "contact_id")
    op.alter_column("contacts_logs", "contact_id_int", new_column_name="contact_id")
    op.create_foreign_key(
        "contacts_logs_contact_id_fkey",
        "contacts_logs",
        "contacts",
        ["contact_id"],
        ["id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def _cutover_contacts_own_id() -> None:
    op.drop_constraint(f"{TABLE}_pkey", TABLE, type_="primary")
    op.drop_column(TABLE, "id")
    op.execute(f"DROP SEQUENCE IF EXISTS {SEQUENCE_NAME}")
    op.alter_column(TABLE, "id_uuid", new_column_name="id")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {TABLE}_pkey "
        f"PRIMARY KEY USING INDEX {TABLE}_id_uuid_key"
    )


def _revert_contacts_own_id() -> None:
    for referrer in REFERRERS:
        op.drop_constraint(referrer.name, referrer.child_table, type_="foreignkey")

    op.drop_constraint(f"{TABLE}_pkey", TABLE, type_="primary")
    op.alter_column(TABLE, "id", new_column_name="id_uuid")
    op.add_column(TABLE, sa.Column("id", sa.Integer(), nullable=True))
    op.execute(f"CREATE SEQUENCE {SEQUENCE_NAME} OWNED BY {TABLE}.id")
    op.execute(f"UPDATE {TABLE} SET id = nextval('{SEQUENCE_NAME}')")  # noqa: S608
    op.alter_column(
        TABLE,
        "id",
        nullable=False,
        server_default=sa.text(f"nextval('{SEQUENCE_NAME}'::regclass)"),
    )
    op.create_primary_key(f"{TABLE}_pkey", TABLE, ["id"])
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY {TABLE}_id_uuid_key ON {TABLE} (id_uuid)"
        )

    for referrer in REFERRERS:
        op.create_foreign_key(
            referrer.name,
            referrer.child_table,
            TABLE,
            [referrer.child_column],
            ["id_uuid"],
            ondelete=referrer.ondelete,
            onupdate=referrer.onupdate,
        )


def _add_contacts_modified_by_fk() -> None:
    op.execute("UPDATE contacts SET modified_by = NULL WHERE modified_by = 0")
    op.add_column("contacts", sa.Column("modified_by_uuid", sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE contacts c SET modified_by_uuid = m.id_uuid "
        "FROM members m WHERE c.modified_by = m.id"
    )
    op.drop_column("contacts", "modified_by")
    op.alter_column("contacts", "modified_by_uuid", new_column_name="modified_by")
    op.create_foreign_key(
        MODIFIED_BY_FK,
        "contacts",
        "members",
        ["modified_by"],
        ["id_uuid"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def _revert_contacts_modified_by_fk() -> None:
    op.add_column("contacts", sa.Column("modified_by_int", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE contacts c SET modified_by_int = m.id "
        "FROM members m WHERE c.modified_by = m.id_uuid"
    )
    op.drop_constraint(MODIFIED_BY_FK, "contacts", type_="foreignkey")
    op.drop_column("contacts", "modified_by")
    op.alter_column("contacts", "modified_by_int", new_column_name="modified_by")


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    _cutover_contacts_logs_contact_id()
    _cutover_contacts_own_id()
    _add_contacts_modified_by_fk()


def downgrade() -> None:
    """Downgrade schema.

    Not loss-free: the own-PK revert gets a freshly created sequence with
    no relationship to any UUID that may already have circulated (same
    caveat as every other Final-Cutover downgrade in this series), and
    contacts.modified_by rows that were 0 before upgrade() are NULL after
    downgrade() too, since the original sentinel can't be distinguished
    from a genuine NULL any more. Emergency rollback shortly after deploy
    only, never a production path.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")
    _revert_contacts_modified_by_fk()
    _revert_contacts_own_id()
    _revert_contacts_logs_contact_id()
