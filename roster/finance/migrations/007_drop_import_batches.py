"""Drop verified-dead finance table: import_batches (and its FK from transactions).

Revision ID: finance_007
Revises: finance_006
Create Date: 2026-06-12 00:00:00.000000

Table has 0 runtime code references and 0 rows in the dev database.
CREATE location: 006_intelligence_tables.py (finance_006)

FK dependency:
  transactions.import_batch_id → import_batches.id (FK name: fk_txn_import_batch)
  Must drop the FK constraint before dropping import_batches.

Guards:
  - ALTER TABLE … DROP CONSTRAINT IF EXISTS handles missing FK safely.
  - DROP TABLE IF EXISTS handles already-absent table safely.
  - All statements are idempotent.

Downgrade recreates the original import_batches schema (finance_006) including
the status CHECK constraint and the transactions FK. No data to restore.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_007"
down_revision = "finance_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the FK on transactions before dropping the referenced table.
    op.execute("ALTER TABLE transactions DROP CONSTRAINT IF EXISTS fk_txn_import_batch")
    op.execute("DROP TABLE IF EXISTS import_batches")


def downgrade() -> None:
    # Recreate the original schema from finance_006 (006_intelligence_tables.py)
    # and restore the FK. No data to restore.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS import_batches (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source              TEXT NOT NULL,
            filename            TEXT,
            account_id          UUID REFERENCES accounts(id) ON DELETE SET NULL,
            status              TEXT NOT NULL DEFAULT 'pending',
            row_count           INTEGER NOT NULL DEFAULT 0,
            imported_count      INTEGER NOT NULL DEFAULT 0,
            skipped_count       INTEGER NOT NULL DEFAULT 0,
            error_count         INTEGER NOT NULL DEFAULT 0,
            completed_at        TIMESTAMPTZ,
            error_details       JSONB NOT NULL DEFAULT '{}'::jsonb,
            baselines_computed  BOOLEAN NOT NULL DEFAULT false,
            categories_learned  INTEGER NOT NULL DEFAULT 0,
            metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'import_batches_status_check'
                  AND conrelid = 'import_batches'::regclass
            ) THEN
                ALTER TABLE import_batches
                    ADD CONSTRAINT import_batches_status_check
                        CHECK (status IN ('pending', 'running', 'completed', 'failed'));
            END IF;
        END $$
        """
    )
    op.execute(
        """
        ALTER TABLE transactions
        ADD CONSTRAINT fk_txn_import_batch
        FOREIGN KEY (import_batch_id) REFERENCES import_batches(id)
        ON DELETE SET NULL
        """
    )
