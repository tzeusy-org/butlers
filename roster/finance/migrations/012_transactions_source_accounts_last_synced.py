"""transactions_source_accounts_last_synced

Revision ID: finance_012
Revises: finance_011
Create Date: 2026-07-26 00:00:00.000000

bu-8bnn9 (follow-up from PR #3589, bu-ep4ks.16 slice 1). Perception-tier
slices 3-4: adds the provenance/freshness surface the feed-vs-email
reconciliation sweep (``roster/finance/tools/feed_reconciliation.py``) needs.

``transactions.source`` already exists (finance_006, ``NOT NULL DEFAULT
'manual'``) bounded by ``transactions_source_check`` to ``('manual',
'csv_import', 'email', 'api', 'bank_sync')`` -- none of which is an
aggregator-bridge provenance tag, and ``record_transaction`` never sets this
column explicitly (every row defaults to ``'manual'`` today regardless of
origin), so the reconciliation sweep's "email-parsed" side keys off
``source_message_id IS NOT NULL`` instead, not this column. This migration
only widens the bounded set to add ``'aggregator'`` so a future connector
(SimpleFIN Bridge, bu-8bnn9 slice 2 -- deferred, owner must provision a token
first) has a value to write; it does not touch any existing row.

``accounts.last_synced_at`` -- nullable, no default. Populated by an
aggregator connector on each successful sync. Until slice 2 ships, this stays
NULL for every account, which the freshness check reports honestly as
"never_synced" (degraded) rather than fabricating a healthy status -- see
CLAUDE.md "Degraded-Mode Response Envelope".
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_012"
down_revision = "finance_011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Widen the existing transactions_source_check (finance_006) to allow
    # 'aggregator' as a provenance value, guarded the same way finance_006
    # guards its own constraint adds (idempotent under IF NOT EXISTS re-runs).
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'transactions_source_check'
                  AND conrelid = 'transactions'::regclass
            ) THEN
                ALTER TABLE transactions DROP CONSTRAINT transactions_source_check;
            END IF;
            ALTER TABLE transactions
                ADD CONSTRAINT transactions_source_check
                    CHECK (source IN (
                        'manual', 'csv_import', 'email', 'api', 'bank_sync', 'aggregator'
                    ));
        END $$
    """)

    op.execute("""
        ALTER TABLE accounts
            ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE accounts DROP COLUMN IF EXISTS last_synced_at")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'transactions_source_check'
                  AND conrelid = 'transactions'::regclass
            ) THEN
                ALTER TABLE transactions DROP CONSTRAINT transactions_source_check;
            END IF;
            ALTER TABLE transactions
                ADD CONSTRAINT transactions_source_check
                    CHECK (source IN ('manual', 'csv_import', 'email', 'api', 'bank_sync'));
        END $$
    """)
