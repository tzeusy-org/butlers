"""Add reconciled_transaction_id column to finance.bills for bill↔payment link.

Revision ID: finance_009
Revises: finance_008
Create Date: 2026-06-19 00:00:00.000000

Context (epic bu-hpmgo / bead bu-64kfu — Track A, foundation)
--------------------------------------------------------------
Paid bills were staying ``pending`` because there was no deterministic link
between a ``finance.transactions`` debit and the ``finance.bills`` row it
settled.  This column is the storage anchor for that link.

Design notes
------------
* ``reconciled_transaction_id`` **conceptually** references
  ``finance.transactions.id``, but NO FK constraint is enforced.  The spec
  (openspec/changes/finance-bill-payment-reconciliation/design.md) explicitly
  forbids FK enforcement here to prevent cascading deletes from interfering
  with settled bill records and to keep reconciliation logic in the
  application layer where it belongs.
* Additive / nullable: existing rows get NULL by default; no data migration
  is required.
* Idempotent: ``ADD COLUMN IF NOT EXISTS`` makes re-runs safe.
* Reversible: downgrade drops the column unconditionally (``IF EXISTS``).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_009"
down_revision = "finance_008"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# SQL statements — exposed at module level so tests can import and verify them
# without duplicating the SQL (avoids drift between migration and test).
# ---------------------------------------------------------------------------

UPGRADE_SQL: tuple[str, ...] = (
    # No FK constraint per spec — see design notes above.
    "ALTER TABLE bills ADD COLUMN IF NOT EXISTS reconciled_transaction_id UUID",
    # Partial index: covers the reverse-lookup "has this transaction already settled a bill?"
    # check (WHERE reconciled_transaction_id = @txn_id) used by the reconciliation engine.
    # Partial (WHERE IS NOT NULL) keeps the index small since most rows are unreconciled.
    "CREATE INDEX IF NOT EXISTS idx_bills_reconciled_transaction_id"
    " ON bills (reconciled_transaction_id)"
    " WHERE reconciled_transaction_id IS NOT NULL",
)

DOWNGRADE_SQL: tuple[str, ...] = (
    "DROP INDEX IF EXISTS idx_bills_reconciled_transaction_id",
    "ALTER TABLE bills DROP COLUMN IF EXISTS reconciled_transaction_id",
)


def upgrade() -> None:
    for stmt in UPGRADE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_SQL:
        op.execute(stmt)
