"""Add action-semantics columns to finance.bills (autopay, predicted, payee_key).

Revision ID: finance_010
Revises: finance_009
Create Date: 2026-06-20 00:00:00.000000

Context (bill/reminder UX cleanup)
----------------------------------
The weekly bills digest treated every obligation identically — auto-debited
bills (GIRO/CPF), pattern-based predictions, and $0 placeholders were all
surfaced as actionable "OVERDUE" items, drowning the few bills that actually
need user action. This migration gives ``finance.bills`` the structure needed
to segment those classes:

* ``autopay``    — bill is auto-debited (GIRO / CPF / card autopay). Informational
                   only; the owner takes no action.
* ``predicted``  — row originated from a pattern-based prediction rather than a
                   confirmed obligation. Promotes the prior ``metadata.predicted``
                   convention to a first-class, queryable column.
* ``payee_key``  — normalized payee used to collapse name variants on upsert
                   ("Endowus" vs "Endowus CPF OA Investment") so the same payee
                   stops proliferating into duplicate rows.

It also enforces the placeholder doctrine at the storage layer: a ``$0`` bill is
a placeholder awaiting reconciliation and can never be ``overdue`` (previously
only documented in AGENTS.md prose and routinely violated).

Design notes
------------
* Additive / nullable-or-defaulted: existing rows get safe defaults; backfill
  populates ``payee_key`` and ``predicted`` from current data/metadata.
* Self-consistent: offending ``$0 + overdue`` rows are normalized to ``pending``
  BEFORE the CHECK constraint is added, so the upgrade cannot fail on live data.
* Idempotent: ``ADD COLUMN IF NOT EXISTS`` / ``IF NOT EXISTS`` index, and the
  constraint is guarded by a pg_constraint existence check.
* Reversible: downgrade drops the constraint, index, and columns unconditionally.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_010"
down_revision = "finance_009"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# SQL statements — exposed at module level so tests can import and verify them
# without duplicating the SQL (avoids drift between migration and test).
#
# NOTE: ``payee_key`` normalization here MUST stay in sync with the Python
# ``_payee_key`` helper in roster/finance/tools/bills.py (lower + collapse
# whitespace + trim + strip trailing period).
# ---------------------------------------------------------------------------

UPGRADE_SQL: tuple[str, ...] = (
    "ALTER TABLE bills ADD COLUMN IF NOT EXISTS autopay BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE bills ADD COLUMN IF NOT EXISTS predicted BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE bills ADD COLUMN IF NOT EXISTS payee_key TEXT",
    # Backfill predicted from the prior metadata convention.
    "UPDATE bills SET predicted = true"
    " WHERE predicted = false"
    " AND (metadata->>'predicted' = 'true' OR metadata->>'source' = 'predict_bills')",
    # Backfill payee_key (matches _payee_key in bills.py).
    "UPDATE bills SET payee_key ="
    " rtrim(btrim(regexp_replace(lower(payee), '\\s+', ' ', 'g')), '.')"
    " WHERE payee_key IS NULL",
    # Normalize illegal $0 placeholders before adding the guard below.
    "UPDATE bills SET status = 'pending' WHERE amount = 0 AND status = 'overdue'",
    # Guard: a zero-amount bill is a placeholder and can never be overdue.
    # Guarded ADD CONSTRAINT (no native IF NOT EXISTS for constraints).
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'bills_zero_amount_not_overdue'
        ) THEN
            ALTER TABLE bills ADD CONSTRAINT bills_zero_amount_not_overdue
                CHECK (NOT (amount = 0 AND status = 'overdue'));
        END IF;
    END $$
    """,
    "CREATE INDEX IF NOT EXISTS idx_bills_payee_key ON bills (payee_key)",
)

DOWNGRADE_SQL: tuple[str, ...] = (
    "DROP INDEX IF EXISTS idx_bills_payee_key",
    "ALTER TABLE bills DROP CONSTRAINT IF EXISTS bills_zero_amount_not_overdue",
    "ALTER TABLE bills DROP COLUMN IF EXISTS payee_key",
    "ALTER TABLE bills DROP COLUMN IF EXISTS predicted",
    "ALTER TABLE bills DROP COLUMN IF EXISTS autopay",
)


def upgrade() -> None:
    for stmt in UPGRADE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_SQL:
        op.execute(stmt)
