"""members: unique email ignoring case, no parent cycles

Revision ID: 7a3e91c4d5f8
Revises: 5c1d7e9a3b42
Create Date: 2026-10-09 21:00:00.000000

Two integrity gaps of the members table, closed together:

1. The address is the sign-in name and every lookup ignores case, but the
   unique index was case-sensitive: "Max@x.at" and "max@x.at" could both be
   stored and a lookup picked one of them. The unique index is now on
   lower(email); the plain index stays for exact lookups. The migration stops
   with the list if such a pair exists.
2. parent_id (the Leibverhaeltnis) could point at the member itself or close a
   longer cycle, and one cycle makes the directory entry of its members loop
   forever. A CHECK refuses the self reference and a trigger refuses any
   cycle. The migration stops if a cycle is already stored.

Both checks of existing data run first: when one fails, nothing is changed and
no lock is taken.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a3e91c4d5f8"
down_revision: str | Sequence[str] | None = "5c1d7e9a3b42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CYCLE_FUNCTION = """
CREATE FUNCTION members_prevent_parent_cycle() RETURNS trigger AS $$
DECLARE
    current_id uuid := NEW.parent_id;
    steps integer := 0;
BEGIN
    WHILE current_id IS NOT NULL LOOP
        IF current_id = NEW.id THEN
            RAISE EXCEPTION 'members.parent_id would close a cycle'
                USING ERRCODE = '23514';
        END IF;
        steps := steps + 1;
        IF steps > 1000 THEN
            RAISE EXCEPTION 'members.parent_id chain is too deep or cyclic'
                USING ERRCODE = '23514';
        END IF;
        SELECT parent_id INTO current_id FROM members WHERE id = current_id;
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_EXISTING_CYCLES = """
WITH RECURSIVE walk(start_id, current_id, steps) AS (
    SELECT id, parent_id, 1 FROM members WHERE parent_id IS NOT NULL
    UNION ALL
    SELECT w.start_id, m.parent_id, w.steps + 1
    FROM walk w JOIN members m ON m.id = w.current_id
    WHERE w.steps < 1000
)
SELECT DISTINCT start_id FROM walk WHERE current_id = start_id
"""


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    clashes = (
        bind.execute(
            sa.text(
                "SELECT lower(email) FROM members WHERE email IS NOT NULL "
                "GROUP BY lower(email) HAVING count(*) > 1"
            )
        )
        .scalars()
        .all()
    )
    if clashes:
        msg = f"Members whose addresses differ only in case: {clashes}"
        raise RuntimeError(msg)
    cycles = bind.execute(sa.text(_EXISTING_CYCLES)).scalars().all()
    if cycles:
        msg = f"Members that are part of a parent cycle: {cycles}"
        raise RuntimeError(msg)

    op.execute("SET LOCAL lock_timeout = '5s'")

    op.drop_index("ix_members_email", table_name="members")
    op.create_index("ix_members_email", "members", ["email"])
    op.create_index(
        "members_email_lower_key", "members", [sa.text("lower(email)")], unique=True
    )

    op.create_check_constraint(
        "members_parent_not_self_check", "members", "parent_id IS DISTINCT FROM id"
    )
    op.execute(_CYCLE_FUNCTION)
    op.execute(
        "CREATE TRIGGER members_parent_cycle "
        "BEFORE INSERT OR UPDATE OF parent_id ON members "
        "FOR EACH ROW WHEN (NEW.parent_id IS NOT NULL) "
        "EXECUTE FUNCTION members_prevent_parent_cycle()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.execute("DROP TRIGGER members_parent_cycle ON members")
    op.execute("DROP FUNCTION members_prevent_parent_cycle()")
    op.drop_constraint("members_parent_not_self_check", "members", type_="check")

    op.drop_index("members_email_lower_key", table_name="members")
    op.drop_index("ix_members_email", table_name="members")
    op.create_index("ix_members_email", "members", ["email"], unique=True)
