"""Add session_id index to switchboard.notifications.

Revision ID: sw_016
Revises: sw_015
Create Date: 2026-06-18 00:00:00.000000

Motivation
----------
The notifications stats endpoint fires a terminal-failure self-join query:

    SELECT COUNT(*) FROM notifications n
    WHERE n.status = 'failed'
    AND NOT (
        n.session_id IS NOT NULL
        AND EXISTS (
            SELECT 1 FROM notifications n2
            WHERE n2.session_id = n.session_id
              AND n2.channel = n.channel
              AND n2.message = n.message
              AND n2.status = 'sent'
              AND n2.created_at > n.created_at
        )
    )

Without an index on session_id, the EXISTS subquery scans all rows of
``notifications`` for each failed row — O(failed_rows * total_rows) in the
worst case.

This partial index (WHERE session_id IS NOT NULL) covers the subquery's
equality predicate and allows the planner to use an index scan for the
EXISTS lookup.  NULL session_ids (notifications from non-session triggers)
are omitted to keep the index compact.

Query-budget note
-----------------
The stats endpoint issues 4+ separate COUNT queries against ``notifications``
on every dashboard visit.  With this index the terminal-failure self-join
drops from O(F * N) sequential scans to O(F * log N) index lookups where
F = failed rows and N = total rows.  At current notification volumes
(estimated < 100k rows) the full endpoint budget stays under 50 ms.

If ``notifications`` grows beyond ~5 M rows, consider adding a composite
index on (session_id, channel, status, created_at DESC) to make the inner
EXISTS lookup index-only.  File a follow-up bead rather than adding it
preemptively.

Reversibility
-------------
Downgrade drops the index — no data loss.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_016"
down_revision = "sw_015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # ix_notifications_session_id
    #    Partial index — excludes NULL rows (notifications without a
    #    linked session, e.g. proactive scheduled notifications).
    #    Covers: terminal-failure self-join EXISTS subquery in stats endpoint.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_notifications_session_id
        ON notifications (session_id)
        WHERE session_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notifications_session_id")
