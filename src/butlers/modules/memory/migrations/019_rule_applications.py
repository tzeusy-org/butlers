"""rule_applications — mem_019

Create the rule_applications audit table per the memory-retention-policy spec.

Each row records a single rule application event with outcome and context.
This provides a learning loop for rule effectiveness beyond simple counter
increments on the rules table.

Table: rule_applications
  id          UUID PK DEFAULT gen_random_uuid()
  tenant_id   TEXT NOT NULL
  rule_id     UUID NOT NULL, FK rules(id) ON DELETE CASCADE
  session_id  UUID nullable
  request_id  TEXT nullable
  outcome     TEXT NOT NULL — one of 'helpful', 'harmful', 'neutral', 'skipped'
  notes       JSONB NOT NULL DEFAULT '{}'
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()

Indexes:
  idx_rule_applications_tenant_rule  (tenant_id, rule_id, created_at DESC)
  idx_rule_applications_outcome      (tenant_id, outcome, created_at DESC)

Revision ID: mem_019
Revises: mem_018
Create Date: 2026-03-10 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_019"
down_revision = "mem_018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------------------
    # Create rule_applications table
    # ---------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS rule_applications (
            id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   TEXT        NOT NULL,
            rule_id     UUID        NOT NULL
                            REFERENCES rules(id) ON DELETE CASCADE,
            session_id  UUID,
            request_id  TEXT,
            outcome     TEXT        NOT NULL,
            notes       JSONB       NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_rule_applications_outcome
                CHECK (outcome IN ('helpful', 'harmful', 'neutral', 'skipped'))
        )
    """)

    # Composite index for per-rule history queries (filterable by tenant + rule).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rule_applications_tenant_rule
        ON rule_applications (tenant_id, rule_id, created_at DESC)
    """)

    # Index for outcome-based queries (dashboards, diagnostics).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rule_applications_outcome
        ON rule_applications (tenant_id, outcome, created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_rule_applications_outcome")
    op.execute("DROP INDEX IF EXISTS idx_rule_applications_tenant_rule")
    op.execute("DROP TABLE IF EXISTS rule_applications")
