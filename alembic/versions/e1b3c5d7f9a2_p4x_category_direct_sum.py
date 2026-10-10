"""p4x: the direct categories of a transaction add up to its amount

Revision ID: e1b3c5d7f9a2
Revises: d8f2a4c6e0b1
Create Date: 2026-10-10 10:00:00.000000

The direct category assignments of a transaction split its amount between
categories, so the active rows (deleted_at IS NULL) of one transaction either
do not exist or add up to the transaction's amount. The application checks it
before it writes; the database now refuses any other state, whichever path
wrote it.

The check is a deferred constraint trigger: it runs when the transaction
commits, because a replacement sets the old rows aside and inserts the new
ones one after the other and passes through states that do not add up. It
fires for every change of a row's amount, of its deleted_at, of the
transaction it belongs to, for inserts and deletes, and for a change of the
transaction's amount.

No data is changed. The migration stops with the offending transactions when
the stored rows already break the rule.
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1b3c5d7f9a2"
down_revision: str | Sequence[str] | None = "d8f2a4c6e0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ASSERT_FUNCTION = """
CREATE FUNCTION p4x_assert_category_direct_sum(transaction_id uuid) RETURNS void AS $$
DECLARE
    transaction_amount numeric;
    direct_total numeric;
BEGIN
    SELECT amount INTO transaction_amount
    FROM p4x_transactions WHERE id = transaction_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    -- Without active rows the sum is NULL, the comparison is NULL and the rule
    -- holds.
    SELECT sum(amount) INTO direct_total
    FROM p4x_category_directs
    WHERE p4x_transaction_id = transaction_id AND deleted_at IS NULL;
    IF direct_total <> transaction_amount THEN
        RAISE EXCEPTION
            'p4x_category_directs of transaction % do not add up: % instead of %',
            transaction_id, direct_total, transaction_amount
            USING ERRCODE = '23514';
    END IF;
END;
$$ LANGUAGE plpgsql
"""

_DIRECTS_FUNCTION = """
CREATE FUNCTION p4x_category_directs_check_sum() RETURNS trigger AS $$
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM p4x_assert_category_direct_sum(NEW.p4x_transaction_id);
    END IF;
    IF TG_OP = 'DELETE'
       OR (TG_OP = 'UPDATE'
           AND OLD.p4x_transaction_id IS DISTINCT FROM NEW.p4x_transaction_id) THEN
        PERFORM p4x_assert_category_direct_sum(OLD.p4x_transaction_id);
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

_TRANSACTIONS_FUNCTION = """
CREATE FUNCTION p4x_transactions_check_direct_sum() RETURNS trigger AS $$
BEGIN
    PERFORM p4x_assert_category_direct_sum(NEW.id);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

_DIRECTS_TRIGGER = """
CREATE CONSTRAINT TRIGGER p4x_category_directs_sum
AFTER INSERT OR UPDATE OF amount, deleted_at, p4x_transaction_id OR DELETE
ON p4x_category_directs
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION p4x_category_directs_check_sum()
"""

_TRANSACTIONS_TRIGGER = """
CREATE CONSTRAINT TRIGGER p4x_transactions_direct_sum
AFTER UPDATE OF amount ON p4x_transactions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW WHEN (OLD.amount IS DISTINCT FROM NEW.amount)
EXECUTE FUNCTION p4x_transactions_check_direct_sum()
"""

_MISMATCHES = """
SELECT t.id FROM p4x_transactions t
JOIN (
    SELECT p4x_transaction_id, sum(amount) AS total
    FROM p4x_category_directs WHERE deleted_at IS NULL
    GROUP BY p4x_transaction_id
) d ON d.p4x_transaction_id = t.id
WHERE d.total <> t.amount
"""


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    mismatches = bind.execute(sa.text(_MISMATCHES)).scalars().all()
    if mismatches:
        msg = f"Transactions whose direct categories do not add up: {mismatches}"
        raise RuntimeError(msg)

    op.execute("SET LOCAL lock_timeout = '5s'")

    op.execute(_ASSERT_FUNCTION)
    op.execute(_DIRECTS_FUNCTION)
    op.execute(_TRANSACTIONS_FUNCTION)
    op.execute(_DIRECTS_TRIGGER)
    op.execute(_TRANSACTIONS_TRIGGER)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.execute("DROP TRIGGER p4x_transactions_direct_sum ON p4x_transactions")
    op.execute("DROP TRIGGER p4x_category_directs_sum ON p4x_category_directs")
    op.execute("DROP FUNCTION p4x_transactions_check_direct_sum()")
    op.execute("DROP FUNCTION p4x_category_directs_check_sum()")
    op.execute("DROP FUNCTION p4x_assert_category_direct_sum(uuid)")
