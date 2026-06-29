"""iea_layer_confidence_evidence

Revision ID: chronicler_017
Revises: chronicler_016
Create Date: 2026-06-29 00:00:00.000000

Add the Intent / Evidence / Activity (IEA) storage surface to chronicler rows:
``layer`` + ``confidence`` + ``evidence_refs`` on ``chronicler.episodes`` and
``layer`` on ``chronicler.point_events`` (bu-66v7ff, tasks.md S3).

Architecture note
-----------------
The IEA reframe (openspec change ``chronicler-intent-evidence-activity``)
classifies every chronicler row into exactly one layer:

  * ``intent``   - planned blocks (calendar). Shown, but NEVER counted as
                   lived time on their own.
  * ``evidence`` - raw signals consumed from read surfaces (GPS points, HR
                   samples, meals, session markers). Never counted; linkable
                   as an activity's ``evidence_refs``.
  * ``activity`` - inferred / lived time. The ONLY layer any time/balance
                   aggregate counts.

The S3 bead delivers storage + the ongoing write-path stamp only. Counting
(aggregations), confidence derivation, and evidence-chain population land in
later beads (tasks.md S4/S5); the ``confidence`` and ``evidence_refs`` columns
exist here but remain at their conservative defaults.

Schema change
-------------
1. ``episodes.layer TEXT NOT NULL DEFAULT 'evidence'`` with a CHECK constraint
   over (``intent``, ``evidence``, ``activity``).
2. ``episodes.confidence TEXT NOT NULL DEFAULT 'low'`` with a CHECK constraint
   over (``high``, ``medium``, ``low``).
3. ``episodes.evidence_refs JSONB NOT NULL DEFAULT '[]'`` (denormalized
   convenience surface; the canonical chain stays in ``episode_event_links``).
4. ``point_events.layer TEXT NOT NULL DEFAULT 'evidence'`` with the same CHECK
   as (1). Point events are raw signals, so ``evidence`` is both the default
   and the permanent value.
5. Backfill existing ``episodes`` by source: calendar rows
   (``source_name LIKE 'google_calendar%'``) -> ``intent``; every other source
   -> ``activity`` (existing source episodes ARE the activity-layer rows for
   their lane). Existing ``point_events`` keep the ``evidence`` default.
6. Recreate ``v_episodes_corrected`` (DROP + CREATE; the view aggregates over
   ``episode_entities`` so it carries a GROUP BY) and ``v_point_events_corrected``
   to expose the new columns.
7. Partial btree index ``episodes_layer_idx`` on (layer, start_at DESC) for the
   activity-only counting path that lands in the aggregations bead.

Conservative default choice (``evidence``)
-----------------------------------------
The default MUST never cause "uncounted activity" nor "counted intent". Only the
``activity`` layer is counted, so:

  * ``activity`` as the default was rejected: an un-stamped calendar/intent row
    would then be counted -> the exact "calendar = 5h" defect this work fixes
    ("counted intent").
  * ``evidence`` is the only neutral choice that is BOTH never counted (so a
    stray default row can never inflate lived-time totals) AND not "intent" (so
    it is never fabricated as a planned ghost block). Every projection adapter
    stamps its real layer explicitly on the write path, so the default is only a
    safety net for rows no code classified; for those, biasing toward the
    recoverable/visible under-count is safer than the reported over-count.

Backfill is by ``source_name`` and is independent of the column default, so
pre-existing rows are classified correctly regardless of the default.

Downgrade drops the columns, the index, the CHECK constraints, and restores the
chronicler_016 / chronicler_015 view shapes.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_017"
down_revision = "chronicler_016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1-3. Episode layer / confidence / evidence_refs columns ─────────────
    op.execute("""
        ALTER TABLE episodes
        ADD COLUMN IF NOT EXISTS layer TEXT NOT NULL DEFAULT 'evidence'
    """)
    op.execute("""
        ALTER TABLE episodes
        ADD COLUMN IF NOT EXISTS confidence TEXT NOT NULL DEFAULT 'low'
    """)
    op.execute("""
        ALTER TABLE episodes
        ADD COLUMN IF NOT EXISTS evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb
    """)
    op.execute("""
        ALTER TABLE episodes
        DROP CONSTRAINT IF EXISTS episodes_layer_check
    """)
    op.execute("""
        ALTER TABLE episodes
        ADD CONSTRAINT episodes_layer_check
        CHECK (layer IN ('intent', 'evidence', 'activity'))
    """)
    op.execute("""
        ALTER TABLE episodes
        DROP CONSTRAINT IF EXISTS episodes_confidence_check
    """)
    op.execute("""
        ALTER TABLE episodes
        ADD CONSTRAINT episodes_confidence_check
        CHECK (confidence IN ('high', 'medium', 'low'))
    """)

    # ── 4. Point-event layer column (always evidence) ───────────────────────
    op.execute("""
        ALTER TABLE point_events
        ADD COLUMN IF NOT EXISTS layer TEXT NOT NULL DEFAULT 'evidence'
    """)
    op.execute("""
        ALTER TABLE point_events
        DROP CONSTRAINT IF EXISTS point_events_layer_check
    """)
    op.execute("""
        ALTER TABLE point_events
        ADD CONSTRAINT point_events_layer_check
        CHECK (layer IN ('intent', 'evidence', 'activity'))
    """)

    # ── 5. Backfill existing episodes by source ─────────────────────────────
    # Calendar projections are intent; every other source is a lived-activity
    # episode. Point events keep the 'evidence' default (no UPDATE needed).
    op.execute("""
        UPDATE episodes
        SET layer = 'intent'
        WHERE source_name LIKE 'google_calendar%'
    """)
    op.execute("""
        UPDATE episodes
        SET layer = 'activity'
        WHERE source_name NOT LIKE 'google_calendar%'
    """)

    # ── 6a. Recreate v_episodes_corrected to expose the new columns ─────────
    # The view aggregates over episode_entities, so it carries a GROUP BY; the
    # three new columns are added to both the SELECT list and the GROUP BY.
    op.execute("DROP VIEW IF EXISTS v_episodes_corrected")
    op.execute("""
        CREATE VIEW v_episodes_corrected AS
        SELECT
            e.id,
            e.source_name,
            e.source_ref,
            e.episode_type,
            COALESCE(o.corrected_start_at, e.start_at) AS start_at,
            COALESCE(o.corrected_end_at, e.end_at) AS end_at,
            e.precision,
            COALESCE(o.corrected_title, e.title) AS title,
            e.payload,
            COALESCE(o.corrected_privacy, e.privacy) AS privacy,
            e.retention_days,
            COALESCE(o.corrected_tombstone_at, e.tombstone_at) AS tombstone_at,
            e.start_at AS canonical_start_at,
            e.end_at AS canonical_end_at,
            e.title AS canonical_title,
            e.privacy AS canonical_privacy,
            o.corrected_at,
            o.note AS correction_note,
            e.created_at,
            e.updated_at,
            e.layer,
            e.confidence,
            e.evidence_refs,
            COALESCE(
                array_agg(ee.entity_id ORDER BY
                    CASE ee.role
                        WHEN 'owner' THEN 0
                        WHEN 'organizer' THEN 1
                        ELSE 2
                    END,
                    ee.entity_id
                ) FILTER (WHERE ee.entity_id IS NOT NULL),
                '{}'::uuid[]
            ) AS participant_entity_ids
        FROM episodes e
        LEFT JOIN v_latest_overrides o
            ON o.target_kind = 'episode' AND o.target_id = e.id
        LEFT JOIN episode_entities ee
            ON ee.episode_id = e.id
        GROUP BY
            e.id,
            e.source_name,
            e.source_ref,
            e.episode_type,
            e.start_at,
            e.end_at,
            e.precision,
            e.title,
            e.payload,
            e.privacy,
            e.retention_days,
            e.tombstone_at,
            e.created_at,
            e.updated_at,
            e.layer,
            e.confidence,
            e.evidence_refs,
            o.corrected_start_at,
            o.corrected_end_at,
            o.corrected_title,
            o.corrected_privacy,
            o.corrected_tombstone_at,
            o.corrected_at,
            o.note
    """)

    # ── 6b. Recreate v_point_events_corrected to expose layer ───────────────
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
            p.entity_id,
            p.layer
        FROM point_events p
        LEFT JOIN v_latest_overrides o
            ON o.target_kind = 'point_event' AND o.target_id = p.id
    """)

    # ── 7. Index for the activity-only counting path ────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS episodes_layer_idx
        ON episodes (layer, start_at DESC)
        WHERE tombstone_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS episodes_layer_idx")

    # ── Restore v_point_events_corrected without layer ──────────────────────
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
            p.updated_at,
            p.entity_id
        FROM point_events p
        LEFT JOIN v_latest_overrides o
            ON o.target_kind = 'point_event' AND o.target_id = p.id
    """)

    # ── Restore v_episodes_corrected (chronicler_016 shape) ─────────────────
    op.execute("DROP VIEW IF EXISTS v_episodes_corrected")
    op.execute("""
        CREATE VIEW v_episodes_corrected AS
        SELECT
            e.id,
            e.source_name,
            e.source_ref,
            e.episode_type,
            COALESCE(o.corrected_start_at, e.start_at) AS start_at,
            COALESCE(o.corrected_end_at, e.end_at) AS end_at,
            e.precision,
            COALESCE(o.corrected_title, e.title) AS title,
            e.payload,
            COALESCE(o.corrected_privacy, e.privacy) AS privacy,
            e.retention_days,
            COALESCE(o.corrected_tombstone_at, e.tombstone_at) AS tombstone_at,
            e.start_at AS canonical_start_at,
            e.end_at AS canonical_end_at,
            e.title AS canonical_title,
            e.privacy AS canonical_privacy,
            o.corrected_at,
            o.note AS correction_note,
            e.created_at,
            e.updated_at,
            COALESCE(
                array_agg(ee.entity_id ORDER BY
                    CASE ee.role
                        WHEN 'owner' THEN 0
                        WHEN 'organizer' THEN 1
                        ELSE 2
                    END,
                    ee.entity_id
                ) FILTER (WHERE ee.entity_id IS NOT NULL),
                '{}'::uuid[]
            ) AS participant_entity_ids
        FROM episodes e
        LEFT JOIN v_latest_overrides o
            ON o.target_kind = 'episode' AND o.target_id = e.id
        LEFT JOIN episode_entities ee
            ON ee.episode_id = e.id
        GROUP BY
            e.id,
            e.source_name,
            e.source_ref,
            e.episode_type,
            e.start_at,
            e.end_at,
            e.precision,
            e.title,
            e.payload,
            e.privacy,
            e.retention_days,
            e.tombstone_at,
            e.created_at,
            e.updated_at,
            o.corrected_start_at,
            o.corrected_end_at,
            o.corrected_title,
            o.corrected_privacy,
            o.corrected_tombstone_at,
            o.corrected_at,
            o.note
    """)

    op.execute("ALTER TABLE point_events DROP CONSTRAINT IF EXISTS point_events_layer_check")
    op.execute("ALTER TABLE point_events DROP COLUMN IF EXISTS layer")
    op.execute("ALTER TABLE episodes DROP CONSTRAINT IF EXISTS episodes_confidence_check")
    op.execute("ALTER TABLE episodes DROP CONSTRAINT IF EXISTS episodes_layer_check")
    op.execute("ALTER TABLE episodes DROP COLUMN IF EXISTS evidence_refs")
    op.execute("ALTER TABLE episodes DROP COLUMN IF EXISTS confidence")
    op.execute("ALTER TABLE episodes DROP COLUMN IF EXISTS layer")
