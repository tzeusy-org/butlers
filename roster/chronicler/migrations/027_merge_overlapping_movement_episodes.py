"""merge_overlapping_movement_episodes

Revision ID: chronicler_027
Revises: chronicler_026
Create Date: 2026-11-01 00:00:00.000000

One-shot repair for OwnTracks ``movement_episode`` rows that already overlap.

Background
----------
``OwnTracksPointAdapter`` used to start a fresh movement episode whenever a
late or replayed batch began a new segment, even if a stored episode for the
same endpoint already covered that span. The Oct 16 trail shows the result:
two live chunks, 08:14-12:01 and 08:25-09:26. bu-2tsgqb.6 (PR #5113) stops new
duplicates by widening the overlapping stored episode instead. This migration
repairs the rows written before that change, with the same overlap rule.

Overlap rule (identical to ``OwnTracksPointAdapter._merge_overlapping_episode``)
-------------------------------------------------------------------------------
Two live (``tombstone_at IS NULL``) ``owntracks.points`` movement episodes
overlap when they share ``payload.endpoint_identity`` and their closed spans
intersect: ``a.start_at <= b.end_at AND a.end_at >= b.start_at`` (a missing
``end_at`` counts as ``start_at``). Overlap is transitive here: every chain of
overlapping episodes for one endpoint is one group, so the repaired survivor
can never overlap a neighbouring group.

Repair per group
----------------
- **Survivor:** the earliest episode (``start_at``, then ``source_ref``), the
  same winner the adapter picks, so later adapter merges converge on it.
- **Evidence:** the other members' ``episode_event_links`` and
  ``episode_entities`` rows are copied onto the survivor. The originals stay
  on the tombstoned rows as provenance.
- **Survivor span and payload:** ``end_at`` widens to the group's latest end;
  ``end_lat``/``end_lon`` come from the member that ends last. ``point_count``
  counts distinct linked fixes, plus the stored count of any member with no
  linked evidence (the adapter's legacy rule). The title and the denormalized
  ``evidence_refs`` follow the new count and links.
- **Duplicates:** every other member is tombstoned with a reason naming the
  survivor's ``source_ref``.
- **Carryover:** an OwnTracks carryover entry pointing at a group member is
  re-pointed at the survivor's widened span. Otherwise the next batch would
  upsert the tombstoned row and clear its tombstone.

Groups containing an episode with a user correction (an ``overrides`` row)
are skipped and reported in the log: merging would silently move or discard
the owner's correction. The canonical row is never edited around an override.

Idempotency
-----------
After one run no two live movement episodes for an endpoint overlap, so a
second run finds no group and changes nothing. The whole repair is one
statement (a ``DO`` block), so it is applied atomically.

Downgrade
---------
No-op. Tombstones are not reversed, matching chronicler_007/008/010.

Issue: bu-2tsgqb.11 (follow-up to bu-2tsgqb.6, PR #5113)
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_027"
down_revision = "chronicler_026"
branch_labels = None
depends_on = None

_SOURCE_NAME = "owntracks.points"
_EPISODE_TYPE = "movement_episode"
_TOMBSTONE_REASON_PREFIX = "overlapping movement episode merged into "
_TOMBSTONE_REASON_SUFFIX = " (bu-2tsgqb.11)"

# One DO block: the temp tables live on one connection and the repair commits
# or rolls back as a unit. Temp tables are dropped explicitly so the block can
# run twice inside one transaction.
REPAIR_SQL = f"""
DO $repair$
DECLARE
    merged_groups INTEGER;
    skipped_groups INTEGER;
BEGIN
    DROP TABLE IF EXISTS pg_temp.chronicler_027_members;
    DROP TABLE IF EXISTS pg_temp.chronicler_027_survivors;

    CREATE TEMP TABLE chronicler_027_members AS
    WITH active AS (
        SELECT
            e.id,
            e.source_ref,
            e.payload ->> 'endpoint_identity' AS endpoint,
            e.start_at,
            COALESCE(e.end_at, e.start_at) AS end_at,
            e.payload,
            EXISTS (
                SELECT 1 FROM overrides o
                WHERE o.target_kind = 'episode' AND o.target_id = e.id
            ) AS corrected,
            EXISTS (
                SELECT 1 FROM episode_event_links l WHERE l.episode_id = e.id
            ) AS has_links
        FROM episodes e
        WHERE e.source_name = '{_SOURCE_NAME}'
          AND e.episode_type = '{_EPISODE_TYPE}'
          AND e.tombstone_at IS NULL
          AND e.payload ->> 'endpoint_identity' IS NOT NULL
    ),
    ordered AS (
        SELECT
            active.*,
            MAX(end_at) OVER (
                PARTITION BY endpoint
                ORDER BY start_at, source_ref
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ) AS prior_max_end
        FROM active
    ),
    islands AS (
        SELECT
            ordered.*,
            SUM(CASE WHEN prior_max_end IS NULL OR start_at > prior_max_end THEN 1 ELSE 0 END)
                OVER (PARTITION BY endpoint ORDER BY start_at, source_ref) AS island
        FROM ordered
    ),
    grouped AS (
        SELECT
            islands.*,
            FIRST_VALUE(id) OVER (
                PARTITION BY endpoint, island ORDER BY start_at, source_ref
            ) AS survivor_id,
            COUNT(*) OVER (PARTITION BY endpoint, island) AS members,
            BOOL_OR(corrected) OVER (PARTITION BY endpoint, island) AS group_corrected
        FROM islands
    )
    SELECT id, survivor_id, source_ref, start_at, end_at, payload, has_links, group_corrected
    FROM grouped
    WHERE members > 1;

    SELECT COUNT(DISTINCT survivor_id) INTO skipped_groups
    FROM chronicler_027_members WHERE group_corrected;
    DELETE FROM chronicler_027_members WHERE group_corrected;
    SELECT COUNT(DISTINCT survivor_id) INTO merged_groups FROM chronicler_027_members;
    -- USING MESSAGE, not a format string: a literal percent sign would be
    -- re-escaped by the DBAPI paramstyle on the Alembic path.
    RAISE NOTICE USING MESSAGE = 'chronicler_027: merging ' || merged_groups
        || ' overlapping movement group(s); skipped ' || skipped_groups
        || ' group(s) holding a user correction';

    -- Aggregate before relinking: "has no linked evidence" is the pre-repair state.
    CREATE TEMP TABLE chronicler_027_survivors AS
    SELECT
        m.survivor_id,
        MAX(m.end_at) AS end_at,
        (ARRAY_AGG(m.payload ORDER BY m.end_at DESC, m.start_at DESC, m.source_ref DESC))[1]
            AS last_payload,
        SUM(
            CASE
                WHEN NOT m.has_links AND (m.payload ->> 'point_count') ~ '^[0-9]+$'
                THEN (m.payload ->> 'point_count')::INTEGER
                ELSE 0
            END
        ) AS unlinked_points
    FROM chronicler_027_members m
    GROUP BY m.survivor_id;

    INSERT INTO episode_event_links (episode_id, event_id, relation)
    SELECT m.survivor_id, l.event_id, l.relation
    FROM chronicler_027_members m
    JOIN episode_event_links l ON l.episode_id = m.id
    WHERE m.id <> m.survivor_id
    ON CONFLICT DO NOTHING;

    INSERT INTO episode_entities (episode_id, entity_id, role)
    SELECT m.survivor_id, ee.entity_id, ee.role
    FROM chronicler_027_members m
    JOIN episode_entities ee ON ee.episode_id = m.id
    WHERE m.id <> m.survivor_id
    ON CONFLICT DO NOTHING;

    UPDATE episodes e
    SET end_at = GREATEST(COALESCE(e.end_at, e.start_at), s.end_at),
        payload = e.payload
            || jsonb_build_object('point_count', linked.n + s.unlinked_points)
            || jsonb_strip_nulls(jsonb_build_object(
                   'end_lat', s.last_payload -> 'end_lat',
                   'end_lon', s.last_payload -> 'end_lon'
               )),
        title = 'Movement (' || (linked.n + s.unlinked_points) || ' points)',
        evidence_refs = CASE WHEN linked.n > 0 THEN linked.refs ELSE e.evidence_refs END,
        updated_at = now()
    FROM chronicler_027_survivors s
    CROSS JOIN LATERAL (
        SELECT
            COUNT(*)::INTEGER AS n,
            COALESCE(jsonb_agg(ev.id::TEXT ORDER BY ev.occurred_at, ev.id), '[]'::jsonb)
                AS refs
        FROM (
            SELECT DISTINCT p.id, p.occurred_at
            FROM episode_event_links l
            JOIN point_events p ON p.id = l.event_id
            WHERE l.episode_id = s.survivor_id
        ) ev
    ) linked
    WHERE e.id = s.survivor_id;

    UPDATE episodes e
    SET tombstone_at = now(),
        tombstone_reason = '{_TOMBSTONE_REASON_PREFIX}' || survivor.source_ref
            || '{_TOMBSTONE_REASON_SUFFIX}',
        updated_at = now()
    FROM chronicler_027_members m
    JOIN episodes survivor ON survivor.id = m.survivor_id
    WHERE e.id = m.id
      AND m.id <> m.survivor_id
      AND e.tombstone_at IS NULL;

    -- Re-point carryover so the next batch extends the survivor, not a tombstone.
    UPDATE projection_checkpoints pc
    SET carryover = (
            SELECT jsonb_object_agg(
                kv.key,
                CASE
                    WHEN target.id IS NULL THEN kv.value
                    ELSE kv.value || jsonb_strip_nulls(jsonb_build_object(
                        'source_ref', target.source_ref,
                        'start_at', to_jsonb(target.start_at),
                        'end_at', to_jsonb(COALESCE(target.end_at, target.start_at)),
                        'start_lat', target.payload -> 'start_lat',
                        'start_lon', target.payload -> 'start_lon',
                        'point_count', target.payload -> 'point_count'
                    ))
                END
            )
            FROM jsonb_each(pc.carryover) kv
            LEFT JOIN chronicler_027_members m
                ON jsonb_typeof(kv.value) = 'object'
               AND m.source_ref = kv.value ->> 'source_ref'
            LEFT JOIN episodes target ON target.id = m.survivor_id
        ),
        updated_at = now()
    WHERE pc.source_name = '{_SOURCE_NAME}'
      AND jsonb_typeof(pc.carryover) = 'object'
      AND EXISTS (
          SELECT 1
          FROM jsonb_each(pc.carryover) kv
          JOIN chronicler_027_members m
            ON jsonb_typeof(kv.value) = 'object'
           AND m.source_ref = kv.value ->> 'source_ref'
      );

    DROP TABLE pg_temp.chronicler_027_members;
    DROP TABLE pg_temp.chronicler_027_survivors;
END
$repair$
"""


def upgrade() -> None:
    op.execute(REPAIR_SQL)


def downgrade() -> None:
    # Tombstones are not reversed: re-surfacing duplicates would recreate the bug.
    pass
