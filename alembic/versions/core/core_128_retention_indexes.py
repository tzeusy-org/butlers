"""Retention & query-budget: hot-path indexes for sessions and notifications.

Revision ID: core_128
Revises: core_127
Create Date: 2026-06-18 00:00:00.000000

Motivation
----------
Long-running personal data tables have grown without indexes on the columns
that dashboard read-models use for ordering and range-filtering.  Three hot
paths were identified during the bu-dl98i.7.6 retention audit:

1.  sessions.started_at DESC
    Used by every fan-out list query (sessions_v1, timeline_v1) and the
    per-butler analytics endpoint.  Without this index, each fan-out arm
    performs a full table scan + sort — cost grows linearly with session history.

2.  sessions.completed_at DESC (partial: WHERE completed_at IS NOT NULL)
    Used by the activity-feed endpoint:
        SELECT ... FROM sessions WHERE completed_at IS NOT NULL
        ORDER BY completed_at DESC LIMIT $1
    A partial index skips NULL rows (in-flight sessions) so it stays compact
    and is automatically filtered by the WHERE clause.

3.  switchboard.notifications.session_id (partial: WHERE session_id IS NOT NULL)
    Used by the terminal-failure self-join in the notifications stats endpoint:
        WHERE n.session_id IS NOT NULL
        AND EXISTS (SELECT 1 FROM notifications n2
                    WHERE n2.session_id = n.session_id AND ...)
    Without this index the EXISTS subquery degrades to a sequential scan per
    failed notification row.

Idempotency
-----------
All three use ``CREATE INDEX IF NOT EXISTS`` — safe to run against a DB that
already has these indexes (e.g. a dev instance where they were created
manually).

Per-butler sessions indexes
---------------------------
The ``sessions`` table lives in every butler's own schema (not in ``public``).
Alembic runs each butler schema migration in that schema's search_path, so the
un-qualified table name ``sessions`` targets the correct per-butler table.

Switchboard notifications index
--------------------------------
The ``notifications`` table lives in the switchboard schema.  That index is
in a separate migration (switchboard branch sw_016); see
roster/switchboard/migrations/016_notifications_session_id_index.py.
This core migration covers only the sessions indexes.

Query-budget notes
------------------
Fan-out coordinator (sessions_v1.query_session_summaries_fan_out):
  - Fires one COUNT(*) + one ORDER BY started_at DESC per butler per request.
  - With ix_sessions_started_at the sort is index-only; no heap sort needed.
  - Budget at N butlers: O(N * log(rows_per_butler)).  Acceptable for up to
    ~20 butlers and millions of rows per butler.

Activity feed (activity_feed.py):
  - Single butler, LIMIT 50 max.  With ix_sessions_completed_at the planner
    uses an index scan + early stop; typical cost < 1 ms per butler.

Sessions analytics (sessions.py _LATENCY_STATS_SQL):
  - percentile_cont over a date-range window (default 30 days).
  - Range predicate started_at >= NOW() - ($N * INTERVAL '1 day') means the
    planner can use ix_sessions_started_at for the range filter; the aggregate
    then operates over the filtered rows only.
  - Worst-case: window_days=365 on a prolific butler — monitor if latency
    exceeds 500 ms; the caller caps window_days at 365.

Reversibility
-------------
Downgrade drops the three indexes — no data loss.
"""

from __future__ import annotations

from alembic import op

revision = "core_128"
down_revision = "core_127"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. ix_sessions_started_at
    #    Covers: timeline fan-out ORDER BY, sessions list ORDER BY,
    #            analytics date-range filter (started_at >= NOW() - interval).
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sessions_started_at
        ON sessions (started_at DESC)
        """
    )

    # ------------------------------------------------------------------
    # 2. ix_sessions_completed_at
    #    Partial index — excludes NULL rows (in-flight sessions).
    #    Covers: activity_feed WHERE completed_at IS NOT NULL
    #            ORDER BY completed_at DESC LIMIT N.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sessions_completed_at
        ON sessions (completed_at DESC)
        WHERE completed_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sessions_completed_at")
    op.execute("DROP INDEX IF EXISTS ix_sessions_started_at")
