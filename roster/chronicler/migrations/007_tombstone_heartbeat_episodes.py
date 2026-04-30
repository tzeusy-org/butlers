"""tombstone_heartbeat_episodes

Revision ID: chronicler_007
Revises: chronicler_006
Create Date: 2026-04-30 00:00:00.000000

Idempotent data migration that tombstones any pre-existing
``chronicler.episodes`` rows produced from butler-internal operational
sessions (heartbeat ticks, QA probes, healing sessions, and all
scheduler-fired background jobs).

Background
----------
Before bu-x096m (PR #1239), ``CoreSessionsAdapter`` projected *every*
session row into ``chronicler.episodes`` — including rows fired by the
scheduler with ``trigger_source`` values such as ``tick``, ``qa``,
``healing``, or any ``schedule:*`` prefix.  Those rows carry no
"lived past time" signal and inflate lane counts in the Chronicler
dashboard.

bu-x096m added a projection-time filter so *future* sessions with those
sources are skipped.  This migration handles the historical backfill so
that environments self-heal on first Chronicler boot: it sets
``tombstone_at = now()`` and ``tombstone_reason`` on any remaining episode
row produced from an excluded operational session.

Schema change
-------------
This migration also adds the ``tombstone_reason`` column to both the
``episodes`` and ``point_events`` tables (``TEXT NULL``).  No existing rows
are affected by the column addition (``NULL`` is a valid absence-of-reason
value for naturally-expired or manually-tombstoned rows).

Data change
-----------
The filter criteria are imported directly from
``butlers.chronicler.adapters.sessions`` — **not** copied — so the
single source of truth invariant from bu-x096m is maintained.

Matching criteria:
  - ``source_name = 'core.sessions'``
  - ``payload->>'trigger_source' IN ('tick', 'qa', 'healing')``
    OR ``payload->>'trigger_source' LIKE 'schedule:%'``

Idempotency: the ``tombstone_at IS NULL`` guard in the WHERE clause
ensures that re-running this migration touches zero additional rows.

Downgrade
---------
Downgrade is a no-op for the data change (tombstones are not reversed
— removing a tombstone could re-surface rows the user never saw and
create confusing artefacts).  The ``tombstone_reason`` column is
dropped on downgrade.

Issue: bu-6t63s
Siblings: bu-noocq (standalone backfill script), bu-x096m (adapter filter)
"""

from __future__ import annotations

import logging

from alembic import op

# Import the exclusion constants directly from the authoritative source.
# Do NOT copy-paste the list here — see bu-x096m for the single-source-of-truth
# rationale.
from butlers.chronicler.adapters.sessions import (
    EXCLUDED_TRIGGER_SOURCE_PREFIX,
    EXCLUDED_TRIGGER_SOURCES,
)

# revision identifiers, used by Alembic.
revision = "chronicler_007"
down_revision = "chronicler_006"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_TOMBSTONE_REASON = "butler-internal session retroactively excluded (bu-noocq+bu-6t63s)"
_SOURCE_NAME = "core.sessions"


def upgrade() -> None:
    # ── Step 1: add tombstone_reason column to episodes and point_events ──
    op.execute("""
        ALTER TABLE episodes
        ADD COLUMN IF NOT EXISTS tombstone_reason TEXT
    """)
    op.execute("""
        ALTER TABLE point_events
        ADD COLUMN IF NOT EXISTS tombstone_reason TEXT
    """)

    # ── Step 2: tombstone heartbeat/operational episode rows ──────────────
    # Build the exclusion parameters from the authoritative constants.
    exact_sources = sorted(EXCLUDED_TRIGGER_SOURCES)  # deterministic ordering for SQL
    prefix_pattern = EXCLUDED_TRIGGER_SOURCE_PREFIX + "%"

    # Log per-category counts before applying so operators can see what
    # was backfilled in the migration log.
    _log_candidate_counts(exact_sources, prefix_pattern)

    # Build the IN-list literal for the exact-match set so we can embed it
    # in the SQL string (Alembic's op.execute does not accept bind params).
    in_list = ", ".join(f"'{src}'" for src in exact_sources)

    op.execute(f"""
        UPDATE episodes
        SET tombstone_at = now(),
            tombstone_reason = '{_TOMBSTONE_REASON}'
        WHERE source_name = '{_SOURCE_NAME}'
          AND tombstone_at IS NULL
          AND (
              payload->>'trigger_source' IN ({in_list})
              OR payload->>'trigger_source' LIKE '{prefix_pattern}'
          )
    """)


def downgrade() -> None:
    # Data: tombstones are NOT reversed on downgrade — reversing a tombstone
    # could silently re-surface rows the user has never seen.
    # Schema: drop the tombstone_reason column added in upgrade().
    op.execute("""
        ALTER TABLE episodes
        DROP COLUMN IF EXISTS tombstone_reason
    """)
    op.execute("""
        ALTER TABLE point_events
        DROP COLUMN IF EXISTS tombstone_reason
    """)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log_candidate_counts(exact_sources: list[str], prefix_pattern: str) -> None:
    """Log per-category counts of not-yet-tombstoned heartbeat episodes.

    This function runs inside the migration context and uses op.get_bind()
    to read from the DB before the UPDATE is applied.  The counts are purely
    diagnostic; failures here do not abort the migration.
    """
    try:
        bind = op.get_bind()
        in_list = ", ".join(f"'{src}'" for src in exact_sources)
        rows = bind.execute(
            __import__("sqlalchemy").text(f"""
                SELECT
                    payload->>'trigger_source' AS trigger_source,
                    COUNT(*)::int               AS n
                FROM episodes
                WHERE source_name = '{_SOURCE_NAME}'
                  AND tombstone_at IS NULL
                  AND (
                      payload->>'trigger_source' IN ({in_list})
                      OR payload->>'trigger_source' LIKE '{prefix_pattern}'
                  )
                GROUP BY payload->>'trigger_source'
                ORDER BY payload->>'trigger_source'
            """)
        ).fetchall()

        total = 0
        for row in rows:
            ts = row[0] or ""
            count = row[1]
            total += count
            logger.info(
                "chronicler_007: tombstone candidate — trigger_source=%r count=%d",
                ts,
                count,
            )
        logger.info("chronicler_007: total tombstone candidates = %d", total)
    except Exception:
        logger.warning(
            "chronicler_007: could not collect candidate counts (non-fatal)",
            exc_info=True,
        )
