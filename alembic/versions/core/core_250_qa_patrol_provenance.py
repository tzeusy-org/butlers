"""qa_patrols: record patrol origin and enabled-source completion evidence.

Revision ID: core_250
Revises: core_249
Create Date: 2026-09-29 00:00:00.000000

REQ-staffer-qa-008: the independent fleet controller may count only a genuine
scheduled discovery cycle that completed every source enabled by the current
configuration. Adds:

  origin                          'scheduled' | 'operator_synthetic'
  enabled_sources_snapshot        text[] of the enabled set a cycle captured
  enabled_sources_config_digest   bounded stable digest of that set
  discovery_complete              true only for a complete scheduled cycle

All four are nullable and existing rows are not backfilled: a legacy row has
no reliable origin or source-completion evidence, so it stays unknown and never
qualifies. A CHECK keeps ``discovery_complete`` from being claimed by anything
other than a scheduled row that recorded both its snapshot and digest.

The core chain runs once per butler schema against this shared public table,
so every statement is idempotent. Downgrade drops the columns; qualification
then falls back to "no provable patrol", never to a false completion.
"""

from __future__ import annotations

from alembic import op

revision = "core_250"
down_revision = "core_249"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.qa_patrols
            ADD COLUMN IF NOT EXISTS origin TEXT,
            ADD COLUMN IF NOT EXISTS enabled_sources_snapshot TEXT[],
            ADD COLUMN IF NOT EXISTS enabled_sources_config_digest TEXT,
            ADD COLUMN IF NOT EXISTS discovery_complete BOOLEAN
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'public.qa_patrols'::regclass
                  AND conname = 'ck_qa_patrols_provenance'
            ) THEN
                ALTER TABLE public.qa_patrols
                ADD CONSTRAINT ck_qa_patrols_provenance CHECK (
                    (origin IS NULL OR origin IN ('scheduled', 'operator_synthetic'))
                    AND (
                        enabled_sources_config_digest IS NULL
                        OR char_length(enabled_sources_config_digest) <= 80
                    )
                    AND (
                        discovery_complete IS NOT TRUE
                        OR (
                            origin = 'scheduled'
                            AND enabled_sources_snapshot IS NOT NULL
                            AND enabled_sources_config_digest IS NOT NULL
                        )
                    )
                );
            END IF;
        END
        $$
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_qa_patrols_qualifying_completed
        ON public.qa_patrols (enabled_sources_config_digest, completed_at DESC)
        WHERE origin = 'scheduled' AND discovery_complete IS TRUE
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_qa_patrols_qualifying_completed")
    op.execute("ALTER TABLE public.qa_patrols DROP CONSTRAINT IF EXISTS ck_qa_patrols_provenance")
    op.execute("""
        ALTER TABLE public.qa_patrols
            DROP COLUMN IF EXISTS discovery_complete,
            DROP COLUMN IF EXISTS enabled_sources_config_digest,
            DROP COLUMN IF EXISTS enabled_sources_snapshot,
            DROP COLUMN IF EXISTS origin
    """)
