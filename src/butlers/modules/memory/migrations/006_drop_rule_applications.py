"""Drop write-orphaned memory module table: rule_applications.

Revision ID: mem_006
Revises: mem_005
Create Date: 2026-06-28 00:00:00.000000

The rule_applications table was INSERT-only: mark_helpful/mark_harmful wrote an
"additive audit row" per feedback event, but nothing in src/, roster/, or tests/
ever SELECTed from it. The counters it shadowed (applied_count, success_count,
harmful_count on the rules table) are the real source of truth and are untouched
by this drop. No out-of-band consumer is retained.

CREATE location: 001_memory_schema.py (mem_001)

Guards:
  - DROP TABLE IF EXISTS is idempotent and schema-safe.
  - Applied per butler schema; IF EXISTS ensures safety across schemas.

Downgrade recreates the original rule_applications schema and indexes (mem_001).
No rows are restored (the table was write-orphaned audit scaffolding).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_006"
down_revision = "mem_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rule_applications CASCADE")


def downgrade() -> None:
    # Recreate the original schema and indexes from mem_001 (001_memory_schema.py).
    # No rows restored (the table was write-orphaned audit scaffolding).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rule_applications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id TEXT NOT NULL,
            rule_id UUID NOT NULL
                REFERENCES rules(id) ON DELETE CASCADE,
            session_id UUID,
            request_id TEXT,
            outcome TEXT NOT NULL,
            notes JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_rule_applications_outcome
                CHECK (outcome IN ('helpful', 'harmful', 'neutral', 'skipped'))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rule_applications_tenant_rule
        ON rule_applications (tenant_id, rule_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rule_applications_outcome
        ON rule_applications (tenant_id, outcome, created_at DESC)
        """
    )
