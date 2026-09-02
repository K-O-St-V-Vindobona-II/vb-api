"""members final cutover

Revision ID: e097221b6c5f
Revises: 3f82fd850390
Create Date: 2026-09-02 18:00:00.000000

The largest single-table slice in the whole UUID-PK migration: members is
the schema's hub (highest referrer count, self-reference, the auth/
permission layer). Four steps in fixed order:

1. members_logs.member_id Referrer-Cutover: a genuine existing FK still
   pointing at the still-integer members.id - the one referrer of members
   left standing after Wave C, discovered the same way
   contacts_logs.contact_id was discovered before the contacts
   Final-Cutover (3f82fd850390): members_logs.modified_by ("who made the
   edit") got its FK in a prior slice, but member_id ("which member was
   edited", the actual changelog subject) never did.
2. members.parent_id Referrer-Cutover: the table's own self-reference
   (org hierarchy - "who is this member's father"), still pointing at
   members.id. Same Phase-B pattern as any other referrer, just against
   the same table via a self-join.
3. members.modified_by: a bare integer column with no ForeignKey() at
   all (like contacts.modified_by before 3f82fd850390), gets a real FK
   to members.id_uuid. Same 0-as-"no member" legacy sentinel
   (199/340 rows), converted to NULL first for the same reason.
4. members Final-Cutover: own PK moves from the additive id_uuid to id.
   Every other referrer (21 FK columns across 19 tables, plus the two
   self-referencing ones from steps 2-3) already points at id_uuid, so a
   PostgreSQL RENAME COLUMN updates the column their existing foreign
   keys point to automatically - none of those referrer tables need
   touching here.

downgrade() reverses step 4 (dropping and recreating every referrer's FK
around the PK revert, since Postgres refuses to drop a pkey index a live
FK still depends on), then step 3, then step 2, then step 1.
"""

from typing import NamedTuple, Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e097221b6c5f"
down_revision: str | Sequence[str] | None = "3f82fd850390"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "members"
SEQUENCE_NAME = "members_id_seq"


class _ReferrerFk(NamedTuple):
    name: str
    child_table: str
    child_column: str
    ondelete: str
    onupdate: str | None


# All 21 pre-existing referrers (already pointing at id_uuid before this
# slice) plus the two self-referencing FKs added by steps 2-3 above - every
# one of them needs its FK dropped before the PK revert and recreated
# afterward in downgrade(). public_gallery_images is the one asymmetric
# case: its FK was defined without onupdate="CASCADE" back in Slice 12
# (public_gallery_images.created_by, see app/models/public_gallery_image.py),
# preserved here as-is rather than silently tightened.
REFERRERS: list[_ReferrerFk] = [
    _ReferrerFk(
        "archive_file_comments_created_by_fkey",
        "archive_file_comments",
        "created_by",
        "SET NULL",
        "CASCADE",
    ),
    _ReferrerFk(
        "archive_store_items_created_by_fkey",
        "archive_store_items",
        "created_by",
        "SET NULL",
        "CASCADE",
    ),
    _ReferrerFk(
        "badges_members_member_id_fkey",
        "badges_members",
        "member_id",
        "CASCADE",
        "CASCADE",
    ),
    _ReferrerFk(
        "contacts_logs_modified_by_fkey",
        "contacts_logs",
        "modified_by",
        "SET NULL",
        "CASCADE",
    ),
    _ReferrerFk(
        "contacts_modified_by_fkey",
        "contacts",
        "modified_by",
        "SET NULL",
        "CASCADE",
    ),
    _ReferrerFk(
        "keys_members_member_id_fkey",
        "keys_members",
        "member_id",
        "CASCADE",
        "CASCADE",
    ),
    _ReferrerFk(
        "member_change_requests_member_id_fkey",
        "member_change_requests",
        "member_id",
        "CASCADE",
        "CASCADE",
    ),
    _ReferrerFk(
        "member_change_requests_resolved_by_fkey",
        "member_change_requests",
        "resolved_by",
        "SET NULL",
        "CASCADE",
    ),
    _ReferrerFk(
        "members_logs_member_id_fkey",
        "members_logs",
        "member_id",
        "SET NULL",
        "CASCADE",
    ),
    _ReferrerFk(
        "members_logs_modified_by_fkey",
        "members_logs",
        "modified_by",
        "SET NULL",
        "CASCADE",
    ),
    _ReferrerFk(
        "members_oauth2bindings_member_id_fkey",
        "members_oauth2bindings",
        "member_id",
        "CASCADE",
        "CASCADE",
    ),
    _ReferrerFk(
        "members_parent_id_fkey",
        "members",
        "parent_id",
        "SET NULL",
        "CASCADE",
    ),
    _ReferrerFk(
        "members_modified_by_fkey",
        "members",
        "modified_by",
        "SET NULL",
        "CASCADE",
    ),
    _ReferrerFk(
        "members_roles_member_id_fkey",
        "members_roles",
        "member_id",
        "CASCADE",
        "CASCADE",
    ),
    _ReferrerFk(
        "p4x_partners_member_id_fkey",
        "p4x_partners",
        "member_id",
        "RESTRICT",
        "CASCADE",
    ),
    _ReferrerFk(
        "p4x_summary_orders_ordered_by_fkey",
        "p4x_summary_orders",
        "ordered_by",
        "CASCADE",
        "CASCADE",
    ),
    _ReferrerFk(
        "p4x_transactions_delegating_member_id_fkey",
        "p4x_transactions",
        "delegating_member_id",
        "SET NULL",
        "CASCADE",
    ),
    _ReferrerFk(
        "public_gallery_images_created_by_fkey",
        "public_gallery_images",
        "created_by",
        "SET NULL",
        None,
    ),
    _ReferrerFk(
        "request_logs_member_id_fkey",
        "request_logs",
        "member_id",
        "SET NULL",
        "CASCADE",
    ),
    _ReferrerFk(
        "sessions_member_id_fkey",
        "sessions",
        "member_id",
        "CASCADE",
        "CASCADE",
    ),
    _ReferrerFk(
        "standesdb_images_created_by_fkey",
        "standesdb_images",
        "created_by",
        "SET NULL",
        "CASCADE",
    ),
    _ReferrerFk(
        "standesdb_images_owner_member_id_fkey",
        "standesdb_images",
        "owner_member_id",
        "CASCADE",
        "CASCADE",
    ),
]


def _cutover_members_logs_member_id() -> None:
    op.add_column("members_logs", sa.Column("member_id_uuid", sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE members_logs ml SET member_id_uuid = m.id_uuid "
        "FROM members m WHERE ml.member_id = m.id"
    )
    op.drop_constraint(
        "members_logs_member_id_fkey", "members_logs", type_="foreignkey"
    )
    op.drop_column("members_logs", "member_id")
    op.alter_column("members_logs", "member_id_uuid", new_column_name="member_id")
    op.create_foreign_key(
        "members_logs_member_id_fkey",
        "members_logs",
        "members",
        ["member_id"],
        ["id_uuid"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def _revert_members_logs_member_id() -> None:
    op.add_column(
        "members_logs", sa.Column("member_id_int", sa.Integer(), nullable=True)
    )
    op.execute(
        "UPDATE members_logs ml SET member_id_int = m.id "
        "FROM members m WHERE ml.member_id = m.id_uuid"
    )
    op.drop_constraint(
        "members_logs_member_id_fkey", "members_logs", type_="foreignkey"
    )
    op.drop_column("members_logs", "member_id")
    op.alter_column("members_logs", "member_id_int", new_column_name="member_id")
    op.create_foreign_key(
        "members_logs_member_id_fkey",
        "members_logs",
        "members",
        ["member_id"],
        ["id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def _cutover_members_parent_id() -> None:
    op.add_column(TABLE, sa.Column("parent_id_uuid", sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE members child SET parent_id_uuid = parent.id_uuid "
        "FROM members parent WHERE child.parent_id = parent.id"
    )
    op.drop_constraint("members_parent_id_fkey", TABLE, type_="foreignkey")
    op.drop_column(TABLE, "parent_id")
    op.alter_column(TABLE, "parent_id_uuid", new_column_name="parent_id")
    op.create_foreign_key(
        "members_parent_id_fkey",
        TABLE,
        TABLE,
        ["parent_id"],
        ["id_uuid"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def _revert_members_parent_id() -> None:
    op.add_column(TABLE, sa.Column("parent_id_int", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE members child SET parent_id_int = parent.id "
        "FROM members parent WHERE child.parent_id = parent.id_uuid"
    )
    op.drop_constraint("members_parent_id_fkey", TABLE, type_="foreignkey")
    op.drop_column(TABLE, "parent_id")
    op.alter_column(TABLE, "parent_id_int", new_column_name="parent_id")
    op.create_foreign_key(
        "members_parent_id_fkey",
        TABLE,
        TABLE,
        ["parent_id"],
        ["id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def _add_members_modified_by_fk() -> None:
    op.execute("UPDATE members SET modified_by = NULL WHERE modified_by = 0")
    op.add_column(TABLE, sa.Column("modified_by_uuid", sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE members child SET modified_by_uuid = editor.id_uuid "
        "FROM members editor WHERE child.modified_by = editor.id"
    )
    op.drop_column(TABLE, "modified_by")
    op.alter_column(TABLE, "modified_by_uuid", new_column_name="modified_by")
    op.create_foreign_key(
        "members_modified_by_fkey",
        TABLE,
        TABLE,
        ["modified_by"],
        ["id_uuid"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )


def _revert_members_modified_by_fk() -> None:
    op.add_column(TABLE, sa.Column("modified_by_int", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE members child SET modified_by_int = editor.id "
        "FROM members editor WHERE child.modified_by = editor.id_uuid"
    )
    op.drop_constraint("members_modified_by_fkey", TABLE, type_="foreignkey")
    op.drop_column(TABLE, "modified_by")
    op.alter_column(TABLE, "modified_by_int", new_column_name="modified_by")


def _cutover_members_own_id() -> None:
    op.drop_constraint(f"{TABLE}_pkey", TABLE, type_="primary")
    op.drop_column(TABLE, "id")
    op.execute(f"DROP SEQUENCE IF EXISTS {SEQUENCE_NAME}")
    op.alter_column(TABLE, "id_uuid", new_column_name="id")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {TABLE}_pkey "
        f"PRIMARY KEY USING INDEX {TABLE}_id_uuid_key"
    )


def _revert_members_own_id() -> None:
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


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    _cutover_members_logs_member_id()
    _cutover_members_parent_id()
    _add_members_modified_by_fk()
    _cutover_members_own_id()


def downgrade() -> None:
    """Downgrade schema.

    Not loss-free: the own-PK revert gets a freshly created sequence with
    no relationship to any UUID that may already have circulated (same
    caveat as every other Final-Cutover downgrade in this series), and
    members.modified_by rows that were 0 before upgrade() are NULL after
    downgrade() too, since the original sentinel can't be distinguished
    from a genuine NULL any more. Emergency rollback shortly after deploy
    only, never a production path.
    """
    op.execute("SET LOCAL lock_timeout = '5s'")
    _revert_members_own_id()
    _revert_members_modified_by_fk()
    _revert_members_parent_id()
    _revert_members_logs_member_id()
