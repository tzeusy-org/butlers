"""point_events_entity_id

Revision ID: chronicler_015
Revises: chronicler_014
Create Date: 2026-06-01 00:00:00.000000

Add ``entity_id`` column to ``chronicler.point_events`` so owner-entity
attribution can cover meals, Google Health steps, and heart-rate events.

Background
----------
Issue bu-kihe8: point-event adapters (meals, google_health steps,
google_health heart_rate) were excluded from bu-4c1ks owner-entity stamping
because ``PointEvent`` lacked an ``entity_id`` column.  Only ``Episode`` had
it (via migration ``chronicler_013``).

This migration mirrors the ``chronicler_013`` approach for point events:

Schema changes
--------------
- Adds ``entity_id UUID`` (nullable, no FK constraint) to
  ``chronicler.point_events``.  No FK is used for the same reason as in
  ``chronicler_013`` — chronicler may boot before the relationship butler
  schema exists in some environments.
- Recreates ``v_point_events_corrected`` to expose ``entity_id``.
- Adds a partial btree index ``point_events_entity_id_idx`` for efficient
  entity-based filtering (only live, non-tombstoned rows).

Design note
-----------
Point events are always single-owner (meals, step counts, heart-rate
measurements belong exclusively to the device owner).  A join table
(analogous to ``episode_entities``) is not introduced here because there is
no multi-participant case for these event types.  The direct column mirrors
exactly what ``chronicler_013`` did for ``episodes`` before the join table
was layered on in ``chronicler_014``.

The column is nullable: existing point_events without an associated entity
keep ``entity_id = NULL``.  Backfill is handled separately by
``scripts/backfill_point_event_entity_id.py`` (bu-kihe8).

Downgrade reverts all three changes.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_015"
down_revision = "chronicler_014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Add entity_id column to point_events ────────────────────────────────
    op.execute("""
        ALTER TABLE point_events
        ADD COLUMN IF NOT EXISTS entity_id UUID
    """)

    # ── Index for entity_id filtering (partial — only live rows) ────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS point_events_entity_id_idx
        ON point_events (entity_id, occurred_at DESC)
        WHERE tombstone_at IS NULL AND entity_id IS NOT NULL
    """)

    # ── Recreate v_point_events_corrected to expose entity_id ───────────────
    # The new column is appended at the end so existing column positions are
    # preserved.  CREATE OR REPLACE is safe here (point_events view has no
    # column-removal constraint because we only add, not remove).
    op.execute("""
        CREATE OR REPLACE VIEW v_point_events_corrected AS
        SELECT
            p.id,
            p.source_name,
            p.source_ref,
            p.event_type,
            COALESCE(o.corrected_start_at, p.occurred_at) AS occurred_at,
            p.precision,
            COALESCE(o.corrected_title, p.title) AS title,
            p.payload,
            COALESCE(o.corrected_privacy, p.privacy) AS privacy,
            p.retention_days,
            COALESCE(o.corrected_tombstone_at, p.tombstone_at) AS tombstone_at,
            p.occurred_at AS canonical_occurred_at,
            p.title AS canonical_title,
            p.privacy AS canonical_privacy,
            o.corrected_at,
            o.note AS correction_note,
            p.created_at,
            p.updated_at,
            p.entity_id
        FROM point_events p
        LEFT JOIN v_latest_overrides o
            ON o.target_kind = 'point_event' AND o.target_id = p.id
    """)


def downgrade() -> None:
    # Restore v_point_events_corrected without entity_id.
    # DROP + CREATE because CREATE OR REPLACE VIEW cannot remove columns.
    op.execute("DROP VIEW IF EXISTS v_point_events_corrected")
    op.execute("""
        CREATE VIEW v_point_events_corrected AS
        SELECT
            p.id,
            p.source_name,
            p.source_ref,
            p.event_type,
            COALESCE(o.corrected_start_at, p.occurred_at) AS occurred_at,
            p.precision,
            COALESCE(o.corrected_title, p.title) AS title,
            p.payload,
            COALESCE(o.corrected_privacy, p.privacy) AS privacy,
            p.retention_days,
            COALESCE(o.corrected_tombstone_at, p.tombstone_at) AS tombstone_at,
            p.occurred_at AS canonical_occurred_at,
            p.title AS canonical_title,
            p.privacy AS canonical_privacy,
            o.corrected_at,
            o.note AS correction_note,
            p.created_at,
            p.updated_at
        FROM point_events p
        LEFT JOIN v_latest_overrides o
            ON o.target_kind = 'point_event' AND o.target_id = p.id
    """)

    op.execute("DROP INDEX IF EXISTS point_events_entity_id_idx")

    op.execute("""
        ALTER TABLE point_events
        DROP COLUMN IF EXISTS entity_id
    """)
