"""
Create fanout_execution_log table
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_007"
down_revision = "sw_006"
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS fanout_execution_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_channel TEXT NOT NULL,
            source_id TEXT,
            tool_name TEXT NOT NULL,
            fanout_mode TEXT NOT NULL,
            join_policy TEXT NOT NULL,
            abort_policy TEXT NOT NULL,
            plan_payload JSONB NOT NULL,
            execution_payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_fanout_execution_log_created_at
        ON fanout_execution_log (created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_fanout_execution_log_created_at")
    op.execute("DROP TABLE IF EXISTS fanout_execution_log")
