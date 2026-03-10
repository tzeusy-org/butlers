"""fix_memory_policies — mem_020

Correct the memory_policies table schema to match the retention-policy spec.

The mem_017 migration created memory_policies with incorrect columns
(default_ttl_days, max_importance, auto_archive) and incorrect seeded
retention classes ('financial'/'relationship'/'preference'/'permanent').

This migration:
  1. Renames default_ttl_days -> ttl_days (semantics unchanged: NULL means no expiry)
  2. Drops the non-spec columns: max_importance, auto_archive
  3. Adds the missing spec columns:
       decay_rate                DOUBLE PRECISION NOT NULL DEFAULT 0.0
       min_retrieval_confidence  DOUBLE PRECISION NOT NULL DEFAULT 0.2
       archive_before_delete     BOOLEAN          NOT NULL DEFAULT FALSE
       allow_summarization       BOOLEAN          NOT NULL DEFAULT TRUE
  4. Deletes all previously seeded rows and re-seeds with the 8 correct
     retention classes from the spec:
       transient, episodic, operational, personal_profile,
       health_log, financial_log, rule, anti_pattern

Revision ID: mem_020
Revises: mem_019
Create Date: 2026-03-10 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_020"
down_revision = "mem_019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------------------
    # 1. Rename default_ttl_days -> ttl_days
    # ---------------------------------------------------------------------------
    op.execute("ALTER TABLE memory_policies RENAME COLUMN default_ttl_days TO ttl_days")

    # ---------------------------------------------------------------------------
    # 2. Drop columns that are not in the spec
    # ---------------------------------------------------------------------------
    op.execute("ALTER TABLE memory_policies DROP COLUMN IF EXISTS max_importance")
    op.execute("ALTER TABLE memory_policies DROP COLUMN IF EXISTS auto_archive")

    # ---------------------------------------------------------------------------
    # 3. Add the missing spec columns
    # ---------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE memory_policies
            ADD COLUMN IF NOT EXISTS decay_rate               DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            ADD COLUMN IF NOT EXISTS min_retrieval_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.2,
            ADD COLUMN IF NOT EXISTS archive_before_delete    BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS allow_summarization      BOOLEAN NOT NULL DEFAULT TRUE
    """)

    # ---------------------------------------------------------------------------
    # 4. Clear old seeded rows and re-seed with correct spec data
    # ---------------------------------------------------------------------------
    op.execute("DELETE FROM memory_policies")

    op.execute("""
        INSERT INTO memory_policies
            (retention_class, ttl_days, decay_rate, min_retrieval_confidence,
             archive_before_delete, allow_summarization)
        VALUES
            ('transient',        7,    0.1,   0.1,  FALSE, FALSE),
            ('episodic',         30,   0.03,  0.15, FALSE, TRUE),
            ('operational',      NULL, 0.008, 0.2,  FALSE, TRUE),
            ('personal_profile', NULL, 0.0,   0.0,  TRUE,  FALSE),
            ('health_log',       NULL, 0.002, 0.1,  TRUE,  TRUE),
            ('financial_log',    NULL, 0.002, 0.1,  TRUE,  FALSE),
            ('rule',             NULL, 0.01,  0.2,  FALSE, TRUE),
            ('anti_pattern',     NULL, 0.0,   0.0,  FALSE, FALSE)
        ON CONFLICT (retention_class) DO UPDATE SET
            ttl_days                 = EXCLUDED.ttl_days,
            decay_rate               = EXCLUDED.decay_rate,
            min_retrieval_confidence = EXCLUDED.min_retrieval_confidence,
            archive_before_delete    = EXCLUDED.archive_before_delete,
            allow_summarization      = EXCLUDED.allow_summarization
    """)


def downgrade() -> None:
    # Revert columns back to mem_017 state
    op.execute("ALTER TABLE memory_policies RENAME COLUMN ttl_days TO default_ttl_days")
    op.execute("ALTER TABLE memory_policies DROP COLUMN IF EXISTS decay_rate")
    op.execute("ALTER TABLE memory_policies DROP COLUMN IF EXISTS min_retrieval_confidence")
    op.execute("ALTER TABLE memory_policies DROP COLUMN IF EXISTS archive_before_delete")
    op.execute("ALTER TABLE memory_policies DROP COLUMN IF EXISTS allow_summarization")
    op.execute("""
        ALTER TABLE memory_policies
            ADD COLUMN IF NOT EXISTS max_importance FLOAT   NOT NULL DEFAULT 10.0,
            ADD COLUMN IF NOT EXISTS auto_archive   BOOLEAN NOT NULL DEFAULT FALSE
    """)

    # Re-seed with the original mem_017 data
    op.execute("DELETE FROM memory_policies")
    op.execute("""
        INSERT INTO memory_policies
            (retention_class, default_ttl_days, max_importance, auto_archive)
        VALUES
            ('transient',    7,    10.0, FALSE),
            ('operational',  90,   10.0, FALSE),
            ('rule',         365,  10.0, FALSE),
            ('health_log',   730,  10.0, TRUE),
            ('financial',    2555, 10.0, TRUE),
            ('relationship', 1825, 10.0, FALSE),
            ('preference',   365,  10.0, FALSE),
            ('permanent',    NULL, 10.0, FALSE)
        ON CONFLICT (retention_class) DO NOTHING
    """)
