"""Add registry liveness/contract metadata and eligibility transition audit log.

Revision ID: sw_009
Revises: sw_008
Create Date: 2026-02-14 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_009"
down_revision = "sw_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE butler_registry
        ADD COLUMN IF NOT EXISTS eligibility_state TEXT NOT NULL DEFAULT 'active',
        ADD COLUMN IF NOT EXISTS liveness_ttl_seconds INTEGER NOT NULL DEFAULT 300,
        ADD COLUMN IF NOT EXISTS quarantined_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS quarantine_reason TEXT,
        ADD COLUMN IF NOT EXISTS route_contract_min INTEGER NOT NULL DEFAULT 1,
        ADD COLUMN IF NOT EXISTS route_contract_max INTEGER NOT NULL DEFAULT 1,
        ADD COLUMN IF NOT EXISTS capabilities JSONB NOT NULL DEFAULT '[]',
        ADD COLUMN IF NOT EXISTS eligibility_updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_butler_registry_eligibility_state'
            ) THEN
                ALTER TABLE butler_registry
                ADD CONSTRAINT ck_butler_registry_eligibility_state
                CHECK (eligibility_state IN ('active', 'stale', 'quarantined'));
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_butler_registry_liveness_ttl_positive'
            ) THEN
                ALTER TABLE butler_registry
                ADD CONSTRAINT ck_butler_registry_liveness_ttl_positive
                CHECK (liveness_ttl_seconds > 0);
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_butler_registry_route_contract_bounds'
            ) THEN
                ALTER TABLE butler_registry
                ADD CONSTRAINT ck_butler_registry_route_contract_bounds
                CHECK (route_contract_min > 0 AND route_contract_max >= route_contract_min);
            END IF;
        END $$;
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS butler_registry_eligibility_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name TEXT NOT NULL,
            previous_state TEXT NOT NULL,
            new_state TEXT NOT NULL,
            reason TEXT NOT NULL,
            previous_last_seen_at TIMESTAMPTZ,
            new_last_seen_at TIMESTAMPTZ,
            observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_registry_eligibility_log_butler_observed
        ON butler_registry_eligibility_log (butler_name, observed_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_registry_eligibility_log_observed
        ON butler_registry_eligibility_log (observed_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_registry_eligibility_log_observed")
    op.execute("DROP INDEX IF EXISTS idx_registry_eligibility_log_butler_observed")
    op.execute("DROP TABLE IF EXISTS butler_registry_eligibility_log")

    op.execute("""
        ALTER TABLE butler_registry
        DROP CONSTRAINT IF EXISTS ck_butler_registry_route_contract_bounds
    """)
    op.execute("""
        ALTER TABLE butler_registry
        DROP CONSTRAINT IF EXISTS ck_butler_registry_liveness_ttl_positive
    """)
    op.execute("""
        ALTER TABLE butler_registry
        DROP CONSTRAINT IF EXISTS ck_butler_registry_eligibility_state
    """)

    op.execute("""
        ALTER TABLE butler_registry
        DROP COLUMN IF EXISTS eligibility_updated_at,
        DROP COLUMN IF EXISTS capabilities,
        DROP COLUMN IF EXISTS route_contract_max,
        DROP COLUMN IF EXISTS route_contract_min,
        DROP COLUMN IF EXISTS quarantine_reason,
        DROP COLUMN IF EXISTS quarantined_at,
        DROP COLUMN IF EXISTS liveness_ttl_seconds,
        DROP COLUMN IF EXISTS eligibility_state
    """)
