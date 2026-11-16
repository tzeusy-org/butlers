"""The location retention job's run record (bu-s11n0s.7, slice S4b-4c).

Revision ID: chronicler_038
Revises: chronicler_037

``location_retention_runs`` holds one row: the retention job's last start, finish and outcome,
its last success and failure times, the failure's exception class (never a message, which could
quote evidence) and its consecutive failure count. The job commits ``running`` before it does
any work and records the outcome in a separate transaction afterwards, so a failed run is never
hidden behind an earlier success, and a run that never finished is visible as a stale
``running`` row.

It is operational state, not evidence of a privacy decision, so the runtime role may read,
insert and update it, and the downgrade drops it. A re-run finds the table and changes nothing.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "chronicler_038"
down_revision = "chronicler_037"
branch_labels = None
depends_on = None

_ROLE = "butler_chronicler_rw"
_RUNS = "location_retention_runs"


def upgrade() -> None:
    installed = op.get_bind().execute(text(f"SELECT to_regclass('{_RUNS}') IS NOT NULL")).scalar()
    if installed:
        return
    op.execute(
        f"""
        CREATE TABLE {_RUNS} (
            id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            last_started_at TIMESTAMPTZ NOT NULL,
            last_finished_at TIMESTAMPTZ,
            last_outcome TEXT NOT NULL
                CHECK (last_outcome IN ('running', 'succeeded', 'failed')),
            last_success_at TIMESTAMPTZ,
            last_failure_at TIMESTAMPTZ,
            last_error_class TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0)
        )
        """
    )
    op.execute(f"REVOKE ALL ON {_RUNS} FROM PUBLIC, {_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {_RUNS} TO {_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_RUNS}")
