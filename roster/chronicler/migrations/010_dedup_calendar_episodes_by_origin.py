"""dedup_calendar_episodes_by_origin

Revision ID: chronicler_010
Revises: chronicler_009
Create Date: 2026-05-03 00:00:00.000000

Tombstone pre-existing duplicate calendar episodes and reset the
``google_calendar.completed`` projection watermark so the adapter re-projects
every instance under the new stable ``source_ref`` format.

Background
----------
Before this change, ``CalendarCompletedAdapter._project_row`` derived
``source_ref`` from the per-schema instance UUID:

    source_ref = "{schema}.calendar_event_instances:{instance_id}"

When the same Google Calendar event was synced into multiple butler schemas
(every butler with the calendar module enabled keeps its own copy), each
schema generated a unique ``source_ref``, so the upsert key
``(source_name, source_ref)`` could not collapse them into a single
chronicler episode. The same Labour Day all-day event therefore showed up
five times in the Gantt swimlane — once per schema.

The companion code change makes ``source_ref`` derive from the upstream
Google Calendar instance identifier (``origin_instance_ref``):

    source_ref = "calendar:{origin_instance_ref}"

This identifier is stable across schemas and resync rounds, so all five
projections collapse onto one upsert target and the duplicates disappear
prospectively.

This migration handles the historical backfill:

1. Tombstone every old-format episode (``source_ref LIKE
   '%.calendar_event_instances:%'``) so they stop rendering in the
   dashboard.
2. Reset the per-schema ``projection_checkpoints`` watermarks for
   ``source_name = 'google_calendar.completed'`` so the next adapter run
   re-projects every completed instance under the new ``source_ref``
   format.

After both steps run, the dashboard should show exactly one calendar
episode per upstream Google Calendar instance.

Idempotency
-----------
- The tombstone UPDATE is guarded by ``tombstone_at IS NULL`` so re-running
  this migration touches no additional rows.
- The watermark reset is also idempotent: if the watermarks were already
  reset by a prior run, the UPDATE simply rewrites the same NULL value.

Downgrade
---------
Tombstones are not reversed — re-surfacing rows the user has already
stopped seeing would be confusing. The watermark reset is technically
reversible but the original watermark value is not preserved, so
downgrade is a no-op.

Issue: chronicles dashboard duplicate-bars cleanup (sibling fix to
calendar.py source_ref change).
"""

from __future__ import annotations

import logging

from alembic import op

revision = "chronicler_010"
down_revision = "chronicler_009"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_SOURCE_NAME = "google_calendar.completed"
_TOMBSTONE_REASON = "duplicate calendar episode superseded by stable origin_instance_ref source_ref"


def upgrade() -> None:
    _log_candidate_counts()

    op.execute(f"""
        UPDATE episodes
        SET tombstone_at    = now(),
            tombstone_reason = '{_TOMBSTONE_REASON}'
        WHERE source_name = '{_SOURCE_NAME}'
          AND tombstone_at IS NULL
          AND source_ref LIKE '%.calendar_event_instances:%'
    """)

    op.execute(f"""
        UPDATE projection_checkpoints
        SET watermark    = NULL,
            watermark_id = NULL
        WHERE source_name = '{_SOURCE_NAME}'
    """)


def downgrade() -> None:
    pass


def _log_candidate_counts() -> None:
    try:
        bind = op.get_bind()
        from sqlalchemy import text

        rows = bind.execute(
            text(f"""
                SELECT COUNT(*)::int AS n
                FROM episodes
                WHERE source_name = '{_SOURCE_NAME}'
                  AND tombstone_at IS NULL
                  AND source_ref LIKE '%.calendar_event_instances:%'
            """)
        ).fetchall()
        total = rows[0][0] if rows else 0
        logger.info("chronicler_010: old-format calendar episodes to tombstone = %d", total)
    except Exception:
        logger.warning(
            "chronicler_010: could not collect candidate counts (non-fatal)",
            exc_info=True,
        )
