"""create_session_process_logs: ephemeral process-level diagnostics for runtime sessions

Revision ID: core_022
Revises: core_021
Create Date: 2026-03-09 00:00:00.000000

Creates a ``session_process_logs`` table that stores raw process-level
output (stderr, exit code, PID, command) from runtime adapter invocations.
This table is TTL-managed: rows carry an ``expires_at`` timestamp defaulting
to 14 days after creation, and a periodic cleanup job deletes expired rows.

Kept separate from ``sessions`` to avoid storage bloat — the core session
record stays lean while process diagnostics are available for recent
debugging.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_022"
down_revision = "core_021"
branch_labels = None
depends_on = None

_DEFAULT_TTL_INTERVAL = "14 days"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS session_process_logs (
            session_id UUID PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
            pid INTEGER,
            exit_code INTEGER,
            command TEXT,
            stderr TEXT,
            runtime_type TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '{_DEFAULT_TTL_INTERVAL}'
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_session_process_logs_expires_at
        ON session_process_logs (expires_at)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS session_process_logs")
