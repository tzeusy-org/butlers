"""Reconcile finance.recurring_groups to the schema the code reads and writes.

Revision ID: finance_008
Revises: finance_007
Create Date: 2026-06-14 00:00:00.000000

Root cause (bead bu-xqncx)
--------------------------
``detect_recurring`` failed on EVERY call with::

    UndefinedColumnError: column "estimated_frequency" of relation
    "recurring_groups" does not exist

The live table was created by finance_006 with one schema while the write path
(``roster/finance/tools/pattern_recognition.py``), the read path
(``roster/finance/tools/overview.py``), the MCP return contract, and the tests
all consistently expect a DIFFERENT schema. The table is EMPTY (0 rows), so
there is zero data-migration risk in reconciling the DB to the code.

This migration reconciles ``recurring_groups`` to the code-expected schema via
ALTER (not DROP+CREATE) so the ``id`` primary key — and the inbound FK
``transactions.recurring_group_id -> recurring_groups(id)``
(``fk_txn_recurring_group``, ON DELETE SET NULL, created in finance_006) — are
preserved untouched.

Column mapping (finance_006 -> code-expected)
---------------------------------------------
  frequency        -> estimated_frequency  (rename; add CHECK)
  expected_amount  -> avg_amount           (rename; widen to NOT NULL)
  last_seen_at     -> last_seen_date        (rename + TIMESTAMPTZ -> DATE)
  (new)            -> currency CHAR(3) DEFAULT 'USD'
  (new)            -> is_active BOOLEAN NOT NULL DEFAULT true

Dropped finance_006-only columns (verified-dead — zero code references):
  normalized_name, amount_variance, status (+ CHECK), subscription_id (+ FK),
  is_subscription, confidence, transaction_count, metadata

Added (essential for the ON CONFLICT (merchant) upsert in detect_recurring):
  UNIQUE (merchant) -> uq_recurring_groups_merchant

All statements are idempotent (guards / IF [NOT] EXISTS) so the migration is
safe to re-run and safe to run against either the finance_006 shape or an
already-reconciled table.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_008"
down_revision = "finance_007"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# SQL statements (also consumed by tests to avoid SQL drift).
# ---------------------------------------------------------------------------

UPGRADE_SQL: tuple[str, ...] = (
    # --- Drop the FK + column to subscriptions (finance_006-only, dead) ---
    "ALTER TABLE recurring_groups DROP CONSTRAINT IF EXISTS recurring_groups_subscription_id_fkey",
    "ALTER TABLE recurring_groups DROP COLUMN IF EXISTS subscription_id",
    # --- Drop the status CHECK + other dead finance_006-only columns ---
    "ALTER TABLE recurring_groups DROP CONSTRAINT IF EXISTS recurring_groups_status_check",
    "ALTER TABLE recurring_groups DROP COLUMN IF EXISTS status",
    "ALTER TABLE recurring_groups DROP COLUMN IF EXISTS normalized_name",
    "ALTER TABLE recurring_groups DROP COLUMN IF EXISTS amount_variance",
    "ALTER TABLE recurring_groups DROP COLUMN IF EXISTS is_subscription",
    "ALTER TABLE recurring_groups DROP COLUMN IF EXISTS confidence",
    "ALTER TABLE recurring_groups DROP COLUMN IF EXISTS transaction_count",
    "ALTER TABLE recurring_groups DROP COLUMN IF EXISTS metadata",
    # --- Rename frequency -> estimated_frequency (guarded) ---
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'recurring_groups'
              AND table_schema = current_schema()
              AND column_name = 'frequency'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'recurring_groups'
              AND table_schema = current_schema()
              AND column_name = 'estimated_frequency'
        ) THEN
            ALTER TABLE recurring_groups RENAME COLUMN frequency TO estimated_frequency;
        END IF;
    END $$
    """,
    # estimated_frequency is created here when neither column was present.
    "ALTER TABLE recurring_groups ADD COLUMN IF NOT EXISTS estimated_frequency TEXT",
    # Add the frequency CHECK (NULL allowed) — guarded so re-runs are safe.
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'recurring_groups_estimated_frequency_check'
              AND conrelid = 'recurring_groups'::regclass
        ) THEN
            ALTER TABLE recurring_groups
                ADD CONSTRAINT recurring_groups_estimated_frequency_check
                    CHECK (estimated_frequency IS NULL OR estimated_frequency IN (
                        'weekly', 'monthly', 'quarterly', 'yearly', 'custom'
                    ));
        END IF;
    END $$
    """,
    # --- Rename expected_amount -> avg_amount (guarded) ---
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'recurring_groups'
              AND table_schema = current_schema()
              AND column_name = 'expected_amount'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'recurring_groups'
              AND table_schema = current_schema()
              AND column_name = 'avg_amount'
        ) THEN
            ALTER TABLE recurring_groups RENAME COLUMN expected_amount TO avg_amount;
        END IF;
    END $$
    """,
    "ALTER TABLE recurring_groups ADD COLUMN IF NOT EXISTS avg_amount NUMERIC(14, 2)",
    # The table is empty, so SET NOT NULL is safe and contract-correct
    # (pattern_recognition INSERTs avg_amount as NOT NULL).
    "ALTER TABLE recurring_groups ALTER COLUMN avg_amount SET NOT NULL",
    # --- Add currency / is_active ---
    "ALTER TABLE recurring_groups ADD COLUMN IF NOT EXISTS currency CHAR(3) DEFAULT 'USD'",
    "ALTER TABLE recurring_groups ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true",
    # --- Rename last_seen_at (TIMESTAMPTZ) -> last_seen_date (DATE) ---
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'recurring_groups'
              AND table_schema = current_schema()
              AND column_name = 'last_seen_at'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'recurring_groups'
              AND table_schema = current_schema()
              AND column_name = 'last_seen_date'
        ) THEN
            ALTER TABLE recurring_groups RENAME COLUMN last_seen_at TO last_seen_date;
            ALTER TABLE recurring_groups
                ALTER COLUMN last_seen_date TYPE DATE USING last_seen_date::date;
        END IF;
    END $$
    """,
    "ALTER TABLE recurring_groups ADD COLUMN IF NOT EXISTS last_seen_date DATE",
    # next_expected_date already exists as DATE in finance_006; ensure present.
    "ALTER TABLE recurring_groups ADD COLUMN IF NOT EXISTS next_expected_date DATE",
    # --- Essential: UNIQUE (merchant) backing the ON CONFLICT (merchant) upsert ---
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_recurring_groups_merchant ON recurring_groups (merchant)",
)


DOWNGRADE_SQL: tuple[str, ...] = (
    # Drop additions.
    "DROP INDEX IF EXISTS uq_recurring_groups_merchant",
    "ALTER TABLE recurring_groups "
    "DROP CONSTRAINT IF EXISTS recurring_groups_estimated_frequency_check",
    "ALTER TABLE recurring_groups DROP COLUMN IF EXISTS currency",
    "ALTER TABLE recurring_groups DROP COLUMN IF EXISTS is_active",
    # Reverse the renames (best-effort restoration of the finance_006 shape).
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'recurring_groups'
              AND table_schema = current_schema()
              AND column_name = 'estimated_frequency'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'recurring_groups'
              AND table_schema = current_schema()
              AND column_name = 'frequency'
        ) THEN
            ALTER TABLE recurring_groups RENAME COLUMN estimated_frequency TO frequency;
        END IF;
    END $$
    """,
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'recurring_groups'
              AND table_schema = current_schema()
              AND column_name = 'avg_amount'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'recurring_groups'
              AND table_schema = current_schema()
              AND column_name = 'expected_amount'
        ) THEN
            ALTER TABLE recurring_groups ALTER COLUMN avg_amount DROP NOT NULL;
            ALTER TABLE recurring_groups RENAME COLUMN avg_amount TO expected_amount;
        END IF;
    END $$
    """,
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'recurring_groups'
              AND table_schema = current_schema()
              AND column_name = 'last_seen_date'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'recurring_groups'
              AND table_schema = current_schema()
              AND column_name = 'last_seen_at'
        ) THEN
            ALTER TABLE recurring_groups RENAME COLUMN last_seen_date TO last_seen_at;
            ALTER TABLE recurring_groups
                ALTER COLUMN last_seen_at TYPE TIMESTAMPTZ USING last_seen_at::timestamptz;
        END IF;
    END $$
    """,
    # Restore the finance_006-only columns (no data to recover).
    "ALTER TABLE recurring_groups ADD COLUMN IF NOT EXISTS normalized_name TEXT",
    "ALTER TABLE recurring_groups ADD COLUMN IF NOT EXISTS amount_variance NUMERIC(14, 2)",
    "ALTER TABLE recurring_groups ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'recurring_groups_status_check'
              AND conrelid = 'recurring_groups'::regclass
        ) THEN
            ALTER TABLE recurring_groups
                ADD CONSTRAINT recurring_groups_status_check
                    CHECK (status IN ('active', 'inactive', 'paused'));
        END IF;
    END $$
    """,
    "ALTER TABLE recurring_groups ADD COLUMN IF NOT EXISTS subscription_id UUID",
    "ALTER TABLE recurring_groups "
    "ADD COLUMN IF NOT EXISTS is_subscription BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE recurring_groups "
    "ADD COLUMN IF NOT EXISTS confidence NUMERIC(4, 3) NOT NULL DEFAULT 0.5",
    "ALTER TABLE recurring_groups "
    "ADD COLUMN IF NOT EXISTS transaction_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE recurring_groups "
    "ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb",
)


def upgrade() -> None:
    for stmt in UPGRADE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_SQL:
        op.execute(stmt)
