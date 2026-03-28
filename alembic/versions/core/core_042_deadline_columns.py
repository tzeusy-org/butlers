"""deadline_columns: add deadline fields to scheduled_tasks table.

Revision ID: core_042
Revises: core_041
Create Date: 2026-03-28 00:00:00.000000

Extends the ``scheduled_tasks`` table with nullable columns for
deadline-tracking support (temporal-intelligence spec, task 1.1):

  task_type          TEXT     NOT NULL DEFAULT 'cron'
                              CHECK (task_type IN ('cron', 'deadline'))
  target_date        DATE     nullable — the due date for deadline tasks
  lead_time_days     INTEGER  nullable — days before target_date to begin alerting
  alert_thresholds   JSONB    nullable — [{days_before, severity}]
  deadline_status    TEXT     nullable — pending|alerted|escalated|completed|expired
  fired_thresholds   JSONB    nullable — thresholds already dispatched
  depends_on         JSONB    nullable — [task_uuid_str, ...]

All existing rows remain valid: task_type defaults to 'cron', new columns are
nullable so no back-fill is required.

Indexes:
  idx_scheduled_tasks_deadline_status — partial index on deadline tasks,
      covering the deadline evaluation pass in tick() which queries
      WHERE task_type = 'deadline' AND enabled = true.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_042"
down_revision = "core_041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Add new columns to scheduled_tasks (all guarded with IF NOT EXISTS).
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE scheduled_tasks
        ADD COLUMN IF NOT EXISTS task_type TEXT NOT NULL DEFAULT 'cron'
            CHECK (task_type IN ('cron', 'deadline'))
    """)

    op.execute("""
        ALTER TABLE scheduled_tasks
        ADD COLUMN IF NOT EXISTS target_date DATE
    """)

    op.execute("""
        ALTER TABLE scheduled_tasks
        ADD COLUMN IF NOT EXISTS lead_time_days INTEGER
    """)

    op.execute("""
        ALTER TABLE scheduled_tasks
        ADD COLUMN IF NOT EXISTS alert_thresholds JSONB
    """)

    op.execute("""
        ALTER TABLE scheduled_tasks
        ADD COLUMN IF NOT EXISTS deadline_status TEXT
            CHECK (deadline_status IN (
                'pending', 'alerted', 'escalated', 'completed', 'expired'
            ))
    """)

    op.execute("""
        ALTER TABLE scheduled_tasks
        ADD COLUMN IF NOT EXISTS fired_thresholds JSONB
    """)

    op.execute("""
        ALTER TABLE scheduled_tasks
        ADD COLUMN IF NOT EXISTS depends_on JSONB
    """)

    # -------------------------------------------------------------------------
    # 2. Partial index to accelerate deadline tick() evaluation pass.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_deadline_status
        ON scheduled_tasks (deadline_status, target_date)
        WHERE task_type = 'deadline' AND enabled = true
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_scheduled_tasks_deadline_status")
    op.execute("ALTER TABLE scheduled_tasks DROP COLUMN IF EXISTS depends_on")
    op.execute("ALTER TABLE scheduled_tasks DROP COLUMN IF EXISTS fired_thresholds")
    op.execute("ALTER TABLE scheduled_tasks DROP COLUMN IF EXISTS deadline_status")
    op.execute("ALTER TABLE scheduled_tasks DROP COLUMN IF EXISTS alert_thresholds")
    op.execute("ALTER TABLE scheduled_tasks DROP COLUMN IF EXISTS lead_time_days")
    op.execute("ALTER TABLE scheduled_tasks DROP COLUMN IF EXISTS target_date")
    op.execute("ALTER TABLE scheduled_tasks DROP COLUMN IF EXISTS task_type")
