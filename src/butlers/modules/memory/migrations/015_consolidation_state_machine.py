"""consolidation_state_machine

Upgrade the episodes table with a lease-based consolidation state machine.

Changes:
- Add ``leased_until`` (TIMESTAMPTZ nullable) and ``leased_by`` (TEXT nullable)
  for lease-based claiming via FOR UPDATE SKIP LOCKED.
- Add ``dead_letter_reason`` (TEXT nullable) for dead-lettered episodes.
- Add ``next_consolidation_retry_at`` (TIMESTAMPTZ nullable) for exponential
  backoff retry scheduling.
- Rename ``retry_count`` → ``consolidation_attempts`` (INTEGER, NOT NULL, DEFAULT 0).
- Rename ``last_error`` → ``last_consolidation_error`` (TEXT nullable).
- Add CHECK constraint enforcing
  ``consolidation_status IN ('pending','consolidated','failed','dead_letter')``.

Revision ID: mem_015
Revises: mem_014
Create Date: 2026-03-10 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_015"
down_revision = "mem_014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------------------
    # Rename retry_count → consolidation_attempts
    # ---------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE episodes
            RENAME COLUMN retry_count TO consolidation_attempts
    """)

    # ---------------------------------------------------------------------------
    # Rename last_error → last_consolidation_error
    # ---------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE episodes
            RENAME COLUMN last_error TO last_consolidation_error
    """)

    # ---------------------------------------------------------------------------
    # Add new lease / retry / dead-letter columns
    # ---------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE episodes
            ADD COLUMN IF NOT EXISTS leased_until TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS leased_by TEXT,
            ADD COLUMN IF NOT EXISTS dead_letter_reason TEXT,
            ADD COLUMN IF NOT EXISTS next_consolidation_retry_at TIMESTAMPTZ
    """)

    # ---------------------------------------------------------------------------
    # Add CHECK constraint on consolidation_status
    # ---------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE episodes
            ADD CONSTRAINT chk_episodes_consolidation_status
            CHECK (consolidation_status IN ('pending', 'consolidated', 'failed', 'dead_letter'))
    """)

    # ---------------------------------------------------------------------------
    # Index to support lease-based claiming ordered by (tenant_id, butler, created_at, id)
    # ---------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_lease_claim
        ON episodes (tenant_id, butler, created_at, id)
        WHERE consolidation_status = 'pending'
    """)


def downgrade() -> None:
    # Drop lease-claim index
    op.execute("DROP INDEX IF EXISTS idx_episodes_lease_claim")

    # Drop CHECK constraint
    op.execute("""
        ALTER TABLE episodes
            DROP CONSTRAINT IF EXISTS chk_episodes_consolidation_status
    """)

    # Drop added columns
    op.execute("""
        ALTER TABLE episodes
            DROP COLUMN IF EXISTS next_consolidation_retry_at,
            DROP COLUMN IF EXISTS dead_letter_reason,
            DROP COLUMN IF EXISTS leased_by,
            DROP COLUMN IF EXISTS leased_until
    """)

    # Rename back last_consolidation_error → last_error
    op.execute("""
        ALTER TABLE episodes
            RENAME COLUMN last_consolidation_error TO last_error
    """)

    # Rename back consolidation_attempts → retry_count
    op.execute("""
        ALTER TABLE episodes
            RENAME COLUMN consolidation_attempts TO retry_count
    """)
