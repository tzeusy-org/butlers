"""session_process_logs: add retry diagnostics and result provenance columns.

Revision ID: core_070
Revises: core_069
Create Date: 2026-04-11 00:00:00.000000

Adds four nullable columns to ``session_process_logs`` so that the Codex
runtime retry path and the spawner failure path can surface enough state
for debugging without requiring the operator to grep daemon logs:

  ``retry_attempted``  BOOLEAN DEFAULT NULL
      True when the MCP-discovery retry was triggered (mcp_failed condition).
      NULL when no retry was attempted (either no MCP servers, or MCP succeeded
      first time, or a non-Codex runtime).

  ``retry_succeeded``  BOOLEAN DEFAULT NULL
      True when the retry subprocess produced at least one real MCP tool call.
      False when both the first and second runs produced 0 MCP tool calls.
      NULL when retry was not attempted.

  ``result_source``  TEXT DEFAULT NULL
      Provenance tag for which subprocess run was used as the final result.
      One of ``'first'`` (used before or after retry failed) or
      ``'retry'`` (retry succeeded and its output was used).
      NULL when retry was not attempted.

  ``attempt_count``  INTEGER DEFAULT NULL
      Number of subprocess runs made in total (1 or 2 for Codex; NULL for
      runtimes that do not retry).

All columns default to NULL so that existing rows are unaffected and the
ALTER TABLE does not require a full table rewrite.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_070"
down_revision = "core_069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE session_process_logs
            ADD COLUMN IF NOT EXISTS retry_attempted BOOLEAN DEFAULT NULL
    """)

    op.execute("""
        ALTER TABLE session_process_logs
            ADD COLUMN IF NOT EXISTS retry_succeeded BOOLEAN DEFAULT NULL
    """)

    op.execute("""
        ALTER TABLE session_process_logs
            ADD COLUMN IF NOT EXISTS result_source TEXT DEFAULT NULL
    """)

    op.execute("""
        ALTER TABLE session_process_logs
            ADD COLUMN IF NOT EXISTS attempt_count INTEGER DEFAULT NULL
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE session_process_logs
            DROP COLUMN IF EXISTS attempt_count
    """)

    op.execute("""
        ALTER TABLE session_process_logs
            DROP COLUMN IF EXISTS result_source
    """)

    op.execute("""
        ALTER TABLE session_process_logs
            DROP COLUMN IF EXISTS retry_succeeded
    """)

    op.execute("""
        ALTER TABLE session_process_logs
            DROP COLUMN IF EXISTS retry_attempted
    """)
