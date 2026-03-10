"""tenant_request_lineage

Add tenant_id, request_id, retention_class, and sensitivity columns to the
episodes, facts, and rules tables.  These four columns provide multi-tenant
isolation, request trace correlation, policy-driven lifecycle management, and
data classification.

Column defaults per table:
- episodes: retention_class DEFAULT 'transient'
- facts:    retention_class DEFAULT 'operational'
- rules:    retention_class DEFAULT 'rule'

All three tables share:
- tenant_id:   TEXT NOT NULL DEFAULT 'owner'
- request_id:  TEXT (nullable)
- sensitivity: TEXT NOT NULL DEFAULT 'normal'

Existing rows are backfilled via the column DEFAULT values — no separate
UPDATE step is required.

Tenant-scoped indexes are added to make (tenant_id, ...) the primary
access pattern for all queries:

- idx_episodes_tenant_butler_status_created
    (tenant_id, butler, consolidation_status, created_at)
- idx_facts_tenant_scope_validity
    (tenant_id, scope, validity) PARTIAL WHERE validity='active'
- idx_rules_tenant_scope_maturity
    (tenant_id, scope, maturity)

Revision ID: mem_014
Revises: mem_013
Create Date: 2026-03-10 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_014"
down_revision = "mem_013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------------------
    # episodes table
    # ---------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE episodes
            ADD COLUMN IF NOT EXISTS tenant_id   TEXT NOT NULL DEFAULT 'owner',
            ADD COLUMN IF NOT EXISTS request_id  TEXT,
            ADD COLUMN IF NOT EXISTS retention_class TEXT NOT NULL DEFAULT 'transient',
            ADD COLUMN IF NOT EXISTS sensitivity TEXT NOT NULL DEFAULT 'normal'
    """)

    # Tenant-scoped composite index replacing the old butler/status/created pattern.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_tenant_butler_status_created
        ON episodes (tenant_id, butler, consolidation_status, created_at)
    """)

    # ---------------------------------------------------------------------------
    # facts table
    # ---------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE facts
            ADD COLUMN IF NOT EXISTS tenant_id   TEXT NOT NULL DEFAULT 'owner',
            ADD COLUMN IF NOT EXISTS request_id  TEXT,
            ADD COLUMN IF NOT EXISTS retention_class TEXT NOT NULL DEFAULT 'operational',
            ADD COLUMN IF NOT EXISTS sensitivity TEXT NOT NULL DEFAULT 'normal'
    """)

    # Tenant-scoped partial index on active facts.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_tenant_scope_validity
        ON facts (tenant_id, scope, validity)
        WHERE validity = 'active'
    """)

    # ---------------------------------------------------------------------------
    # rules table
    # ---------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE rules
            ADD COLUMN IF NOT EXISTS tenant_id   TEXT NOT NULL DEFAULT 'owner',
            ADD COLUMN IF NOT EXISTS request_id  TEXT,
            ADD COLUMN IF NOT EXISTS retention_class TEXT NOT NULL DEFAULT 'rule',
            ADD COLUMN IF NOT EXISTS sensitivity TEXT NOT NULL DEFAULT 'normal'
    """)

    # Tenant-scoped composite index.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rules_tenant_scope_maturity
        ON rules (tenant_id, scope, maturity)
    """)


def downgrade() -> None:
    # Drop new indexes first, then columns.
    op.execute("DROP INDEX IF EXISTS idx_rules_tenant_scope_maturity")
    op.execute("DROP INDEX IF EXISTS idx_facts_tenant_scope_validity")
    op.execute("DROP INDEX IF EXISTS idx_episodes_tenant_butler_status_created")

    op.execute("""
        ALTER TABLE rules
            DROP COLUMN IF EXISTS sensitivity,
            DROP COLUMN IF EXISTS retention_class,
            DROP COLUMN IF EXISTS request_id,
            DROP COLUMN IF EXISTS tenant_id
    """)

    op.execute("""
        ALTER TABLE facts
            DROP COLUMN IF EXISTS sensitivity,
            DROP COLUMN IF EXISTS retention_class,
            DROP COLUMN IF EXISTS request_id,
            DROP COLUMN IF EXISTS tenant_id
    """)

    op.execute("""
        ALTER TABLE episodes
            DROP COLUMN IF EXISTS sensitivity,
            DROP COLUMN IF EXISTS retention_class,
            DROP COLUMN IF EXISTS request_id,
            DROP COLUMN IF EXISTS tenant_id
    """)
