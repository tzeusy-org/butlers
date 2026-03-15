"""ingestion_events: add status and error_detail columns

Revision ID: core_032
Revises: core_031
Create Date: 2026-03-15 00:00:00.000000

Adds a mutable ``status`` column to ``shared.ingestion_events`` so that
routing failures can be recorded and later replayed.  Previously, every row
in this table was implicitly ``'ingested'``; now the column makes the state
explicit and allows the transition to ``'failed'`` (routing did not succeed).

Also adds ``error_detail TEXT`` to capture the reason for failure.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_032"
down_revision = "core_031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE shared.ingestion_events
        ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'ingested'
    """)
    op.execute("""
        ALTER TABLE shared.ingestion_events
        ADD COLUMN IF NOT EXISTS error_detail TEXT
    """)
    # Constraint on allowed status values
    op.execute("""
        DO $$
        BEGIN
            ALTER TABLE shared.ingestion_events
            ADD CONSTRAINT ck_ingestion_events_status
            CHECK (status IN ('ingested', 'failed'));
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END $$
    """)
    # Index for filtering by status (the common query path)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ingestion_events_status
        ON shared.ingestion_events (status)
        WHERE status != 'ingested'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS shared.ix_ingestion_events_status")
    op.execute("""
        ALTER TABLE shared.ingestion_events
        DROP CONSTRAINT IF EXISTS ck_ingestion_events_status
    """)
    op.execute("ALTER TABLE shared.ingestion_events DROP COLUMN IF EXISTS error_detail")
    op.execute("ALTER TABLE shared.ingestion_events DROP COLUMN IF EXISTS status")
