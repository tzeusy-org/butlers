"""reset_watermarks_for_old_session_titles

Revision ID: chronicler_009
Revises: chronicler_008
Create Date: 2026-04-30 00:00:00.000000

Idempotent data migration that resets ``projection_checkpoints`` watermarks
for ``core.sessions`` schemas that still have pre-bu-fkqv0 episode titles.

Background
----------
PR #1300 (bu-fkqv0) shipped a new title-resolution path in
``CoreSessionsAdapter._compute_episode_title`` that produces
``'Conversation with {display_name}'`` (or ``'Conversation via {channel}'``)
for route-triggered sessions where a contact is resolvable.  Before that
change, those same episodes were titled ``'{schema} session'`` — the generic
legacy fallback for all NULL/unrecognised trigger_source values.

Because ``CoreSessionsAdapter`` uses per-schema watermarks stored in
``projection_checkpoints (source_name='core.sessions', subsource=<schema>)``,
and the watermark for each schema has already advanced past the affected rows,
those rows will NOT be re-projected on a normal adapter run.  This migration
resets each affected schema's watermark so that the next adapter run
re-projects them, picking up the new title-resolution logic.

Affected rows
-------------
Episodes matching ALL of:
  - ``source_name = 'core.sessions'``
  - ``payload->>'trigger_source' = 'route'``
  - ``title LIKE '% session'``   (heuristic: any title ending in " session")
  - ``tombstone_at IS NULL``

The title heuristic ``'% session'`` is deliberately broad enough to capture
all known pre-bu-fkqv0 title patterns (``'{schema} session'``) while being
narrow enough to avoid touching user-corrected episodes (which would carry a
human-readable override, not the raw adapter-generated fallback).

Conservative scope decision
---------------------------
All schemas with matching episodes are reset, not just schemas known to have
a resolvable contact.  Rationale:

1. Re-projection is idempotent: ``upsert_episode`` uses ``ON CONFLICT (source_name,
   source_ref) DO UPDATE``, so re-projecting an episode that already has the
   correct title simply writes the same title again — no observable side effect.
2. If a contact is NOT resolvable at re-projection time, the new adapter code
   produces ``'Conversation via {channel}'`` (or ``'Conversation via unknown
   channel'``), which is still an improvement over the bare ``'{schema} session'``
   fallback.
3. Keeping the scope broad means a future contact registration will trigger
   correct re-titling on the next adapter run without a follow-up migration.

Mechanism
---------
For each schema S that has affected episodes, set:
  ``projection_checkpoints.watermark = MIN(episode.start_at) - interval '1 second'``
  ``projection_checkpoints.watermark_id = NULL``
where MIN is taken over affected episodes for schema S.

Using ``start_at - 1 second`` (not ``created_at``) because the adapter's
watermark is derived from ``sessions.started_at``, which equals the episode's
``start_at``.  Setting the watermark to one second before the earliest affected
episode guarantees that the re-projection query
``WHERE started_at > $watermark`` will include that episode's source session.

Idempotency
-----------
After the migration runs and the adapter re-projects the affected rows, those
episodes will have new titles (``'Conversation with ...'`` or ``'Conversation
via ...'``).  They will no longer match ``title LIKE '% session'``, so a second
migration run finds zero candidate schemas and emits no UPDATE.

A per-schema guard further ensures safety: if the existing watermark is ALREADY
less than ``MIN(start_at) - 1 second`` for a schema (i.e. the watermark was
manually reset before this migration ran), the UPDATE is still a no-op because
the WHERE clause requires ``title LIKE '% session'`` candidates to exist.

Downgrade
---------
No-op.  Watermark resets cannot be meaningfully reversed: the adapter will have
already advanced the watermark on the next run, so any revert would be a stale
value pointing to an arbitrary past position.

Issue: bu-jpf3o
Siblings: bu-fkqv0 (PR #1300 — new title-resolution path)
"""

from __future__ import annotations

import logging

from alembic import op

# revision identifiers, used by Alembic.
revision = "chronicler_009"
down_revision = "chronicler_008"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_SOURCE_NAME = "core.sessions"

# Heuristic title filter: any episode whose title ends with ' session' is a
# pre-bu-fkqv0 row produced by the legacy '{schema} session' fallback.
# A deliberate LIKE pattern (not an exact IN list) is used here because the
# set of schemas is open-ended — we cannot enumerate all butler schema names
# in a migration constant.
_TITLE_LIKE_PATTERN = "% session"

# trigger_source value that identifies route-triggered conversations.
_TRIGGER_SOURCE_ROUTE = "route"


def upgrade() -> None:
    """Reset watermarks for schemas with stale '{schema} session' episode titles.

    For each distinct schema found in affected episodes, sets the
    projection_checkpoints watermark to MIN(start_at) - 1 second so the
    next CoreSessionsAdapter run re-projects those sessions with the new
    bu-fkqv0 title-resolution logic.
    """
    # Log candidate counts before applying so operators can see what was reset.
    _log_candidate_schemas()

    # Reset the watermark for each affected schema in a single UPDATE.
    # The subquery computes, per schema, the earliest affected episode start_at.
    op.execute(f"""
        UPDATE projection_checkpoints pc
        SET watermark    = affected.min_start_at - interval '1 second',
            watermark_id = NULL
        FROM (
            SELECT
                payload->>'schema'                AS schema_name,
                MIN(start_at)                     AS min_start_at
            FROM episodes
            WHERE source_name    = '{_SOURCE_NAME}'
              AND payload->>'trigger_source' = '{_TRIGGER_SOURCE_ROUTE}'
              AND title LIKE '{_TITLE_LIKE_PATTERN}'
              AND tombstone_at IS NULL
            GROUP BY payload->>'schema'
        ) AS affected
        WHERE pc.source_name = '{_SOURCE_NAME}'
          AND pc.subsource   = affected.schema_name
          AND (
              -- Only reset if our computed target is EARLIER than the current
              -- watermark (i.e. the watermark has advanced past the affected rows).
              pc.watermark IS NULL
              OR pc.watermark > affected.min_start_at - interval '1 second'
          )
    """)


def downgrade() -> None:
    # Watermark resets are not reversible.  The adapter will have advanced
    # the watermark on the next run; reverting to an arbitrary past value
    # would cause over-projection, not under-projection.
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log_candidate_schemas() -> None:
    """Log per-schema candidate counts before applying the watermark reset.

    Runs inside the migration context via op.get_bind().  Diagnostic only —
    failures here do not abort the migration.
    """
    try:
        bind = op.get_bind()
        rows = bind.execute(
            __import__("sqlalchemy").text(f"""
                SELECT
                    payload->>'schema'              AS schema_name,
                    COUNT(*)::int                   AS n,
                    MIN(start_at)                   AS earliest_start_at
                FROM episodes
                WHERE source_name    = '{_SOURCE_NAME}'
                  AND payload->>'trigger_source' = '{_TRIGGER_SOURCE_ROUTE}'
                  AND title LIKE '{_TITLE_LIKE_PATTERN}'
                  AND tombstone_at IS NULL
                GROUP BY payload->>'schema'
                ORDER BY payload->>'schema'
            """)
        ).fetchall()

        total = 0
        for row in rows:
            schema_name = row[0] or "<null>"
            count = row[1]
            earliest = row[2]
            total += count
            logger.info(
                "chronicler_009: watermark-reset candidate — "
                "schema=%r count=%d earliest_start_at=%s",
                schema_name,
                count,
                earliest,
            )
        logger.info("chronicler_009: total stale-title episodes across all schemas = %d", total)
        if total == 0:
            logger.info("chronicler_009: no stale-title episodes found — migration is a no-op")
    except Exception:
        logger.warning(
            "chronicler_009: could not collect candidate counts (non-fatal)",
            exc_info=True,
        )
