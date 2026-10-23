# Chronicler Migration Verification

> **Purpose:** Verify Chronicler migration outcomes and historical projection backfills.
> **Audience:** Operators applying or investigating Chronicler migrations.
> **Prerequisites:** [Migration patterns](../data_and_storage/migration-patterns.md) and
> [projection recovery](chronicler-projection-recovery.md).

These migration-specific checks moved from Chronicler's session guidance. They preserve
historical migration preconditions and outcomes; apply them to the named migration's state.
The current [Chronicler spec](../../openspec/specs/butler-chronicler/spec.md) governs runtime authority.
Run verification reads against the intended deployment before considering a repair.

## Backfill (core_085, core_086)

- `core_085_backfill_spotify_owntracks_privacy.py` reclassified Spotify
  session-summary rows from `sensitive` to `normal` (the migration name
  predates the OwnTracks portion landing).
- `core_086_backfill_owntracks_privacy_normal.py` reclassifies existing
  `owntracks.points` episodes and point events from `sensitive` to `normal`.
  The matching adapter change in `OwnTracksPointAdapter` ensures new
  ingestion uses the same default.

## Heartbeat tombstone migration verification (chronicler_007 / bu-6t63s)

Migration `chronicler_007` (`roster/chronicler/migrations/007_tombstone_heartbeat_episodes.py`)
retroactively tombstones any pre-existing `chronicler.episodes` rows produced by
butler-internal operational sessions (tick, qa, healing, schedule:*).

**Verify the migration ran cleanly:**

```sql
-- Should return zero rows after chronicler_007 has been applied.
SELECT
    payload->>'trigger_source' AS trigger_source,
    COUNT(*) AS remaining
FROM chronicler.episodes
WHERE source_name = 'core.sessions'
  AND tombstone_at IS NULL
  AND (
      payload->>'trigger_source' IN ('tick', 'qa', 'healing')
      OR payload->>'trigger_source' LIKE 'schedule:%'
  )
GROUP BY 1
ORDER BY 1;
```

**Verify tombstoned rows carry the expected reason:**

```sql
SELECT
    payload->>'trigger_source' AS trigger_source,
    tombstone_reason,
    COUNT(*) AS n
FROM chronicler.episodes
WHERE source_name = 'core.sessions'
  AND tombstone_at IS NOT NULL
  AND tombstone_reason LIKE '%bu-6t63s%'
GROUP BY 1, 2
ORDER BY 1;
```

Both queries run against the `chronicler` schema. If the first returns non-zero
rows, the migration has not yet run or was skipped; run `alembic upgrade chronicler@head`
to apply it.

## Memory calendar episode tombstone migration (chronicler_008 / bu-aqqx0)

Migration `chronicler_008` (`roster/chronicler/migrations/008_tombstone_memory_calendar_episodes.py`)
retroactively tombstones pre-existing `chronicler.episodes` rows produced from
butler-managed memory housekeeping tasks that were incorrectly projected as
calendar events before bu-daaff (PR #1297).

Affected titles (exact list): `memory_consolidation`, `memory_episode_cleanup`,
`memory_purge_superseded`.  Source: `google_calendar.completed`.

**Requires chronicler_007 to run first** (adds the `tombstone_reason` column).

**Verify the migration ran cleanly:**

```sql
-- Should return zero rows after chronicler_008 has been applied.
SELECT
    title,
    COUNT(*) AS remaining
FROM chronicler.episodes
WHERE source_name = 'google_calendar.completed'
  AND tombstone_at IS NULL
  AND title IN (
      'memory_consolidation',
      'memory_episode_cleanup',
      'memory_purge_superseded'
  )
GROUP BY 1
ORDER BY 1;
```

**Verify tombstoned rows carry the expected reason:**

```sql
SELECT
    title,
    tombstone_reason,
    COUNT(*) AS n
FROM chronicler.episodes
WHERE source_name = 'google_calendar.completed'
  AND tombstone_at IS NOT NULL
  AND tombstone_reason LIKE '%bu-aqqx0%'
GROUP BY 1, 2
ORDER BY 1;
```

Both queries run against the `chronicler` schema. If the first returns non-zero
rows, the migration has not yet run or was skipped; run `alembic upgrade chronicler@head`
to apply it.

## Session title re-projection watermark reset (chronicler_009 / bu-jpf3o)

Migration `chronicler_009` (`roster/chronicler/migrations/009_reset_watermarks_for_old_session_titles.py`)
resets `projection_checkpoints` watermarks for `core.sessions` schemas that still carry
pre-bu-fkqv0 episode titles (`'{schema} session'`).  After the reset, the next
`CoreSessionsAdapter` run re-projects those sessions with the new
`'Conversation with {name}'` / `'Conversation via {channel}'` title-resolution logic.

**Affected rows criteria:**
- `source_name = 'core.sessions'`
- `payload->>'trigger_source' = 'route'`
- `title LIKE '% session'`
- `tombstone_at IS NULL`

**Verify stale titles are gone (spot-check after chronicler_009 + adapter run):**

```sql
-- Should return zero rows once the watermark reset has triggered re-projection.
SELECT
    payload->>'schema'  AS schema_name,
    title,
    COUNT(*)            AS remaining
FROM chronicler.episodes
WHERE source_name             = 'core.sessions'
  AND payload->>'trigger_source' = 'route'
  AND title LIKE '% session'
  AND tombstone_at IS NULL
GROUP BY 1, 2
ORDER BY 1, 2;
```

**Verify watermarks were reset for affected schemas:**

```sql
-- Returns the checkpoint rows for all per-schema core.sessions projections.
-- After chronicler_009 runs, affected schemas show watermark near their
-- earliest route-session start_at.
SELECT
    subsource        AS schema_name,
    watermark,
    watermark_id,
    last_success_at,
    rows_projected
FROM chronicler.projection_checkpoints
WHERE source_name = 'core.sessions'
  AND subsource  != ''
ORDER BY subsource;
```

Both queries run against the `chronicler` schema. If the first returns non-zero
rows after the adapter has run, either the migration has not been applied
(`alembic upgrade chronicler@head`) or the adapter has not yet re-projected
(wait for the next scheduled run or trigger manually).

## Backfilling episode_entities on historical episodes (bu-xuqyo)

Migration `chronicler_014` added the `episode_entities` join table to `chronicler`.
Episodes projected **before** the adapter change (bu-3zve1, PR #1869) have no rows
in `episode_entities`.

> **Note (bu-cfsgy):** the derived `episodes.entity_id` column and its one-time
> backfill scripts (`backfill_episode_entity_id.py`,
> `backfill_ha_presence_entity_id.py`, `backfill_episode_participants.py`) were
> removed when the column was dropped (migration `chronicler_016`). The owner
> entity now lives **only** in `episode_entities` (role='owner'), written at
> projection time. To repopulate historical rows, use the [watermark reset](chronicler-projection-recovery.md#watermark-reset-full-re-projection).

### Verify backfill completeness

```sql
-- Non-zero means episode_entities has been populated.
SELECT COUNT(*) AS total_links FROM chronicler.episode_entities;

-- Episodes with no episode_entities rows (expected to be empty after backfill).
SELECT e.id, e.payload->>'schema' AS schema
FROM chronicler.episodes e
LEFT JOIN chronicler.episode_entities ee ON ee.episode_id = e.id
WHERE e.source_name = 'google_calendar.completed'
  AND e.tombstone_at IS NULL
  AND ee.episode_id IS NULL;
```

## Migration 014: episode_entities join table (bu-t0130)

Migration `chronicler_014` (`roster/chronicler/migrations/014_episode_entities.py`)
adds multi-entity support to chronicler episodes.

### Pre-condition

`chronicler_013` must be applied first (it created the original
`v_episodes_corrected` shape that 014 extends). Note: the derived
`episodes.entity_id` column 013 added was later dropped by `chronicler_016`
(bu-cfsgy); the owner entity now lives in `episode_entities`.

### Post-conditions

After `chronicler_014` runs:

- **`chronicler.episode_entities` table** exists with composite PK
  `(episode_id, entity_id)`, an `ON DELETE CASCADE` FK to `chronicler.episodes(id)`,
  and `role TEXT CHECK (role IN ('owner', 'organizer', 'participant'))`.
  No FK on `entity_id` against `public.entities` (matches the existing chronicler
  convention: chronicler boots before the relationship butler schema exists in
  some deployments).
- **`episode_entities_entity_idx`** index exists on `(entity_id, episode_id)` for
  efficient entity-first activity queries.
- **`v_episodes_corrected`** is recreated with a new `participant_entity_ids UUID[]`
  column appended at the end.  The column is **never NULL**: episodes with no rows
  in `episode_entities` return `'{}'::uuid[]` via `COALESCE`.  Array order is
  role-precedence (`owner`=0, `organizer`=1, `participant`=2) then `entity_id ASC`.

### Role-precedence collapse rule

Writers (`CalendarCompletedAdapter`) MUST collapse multiple roles for the same
`(episode_id, entity_id)` pair before writing, keeping the highest-precedence
role:

```
'owner' > 'organizer' > 'participant'
```

Because the PK is `(episode_id, entity_id)`, each `entity_id` appears at most
once per episode.  An attendee who is also the calendar account owner is written
exactly once with `role='owner'`.

### Backfilling episode_entities (bu-xuqyo)

Historical `google_calendar.completed` episodes projected before this adapter
change have no rows in `episode_entities`.  Reset the
`google_calendar.completed` watermark to NULL so the adapter re-projects all
historical meetings (re-writes titles and runs the full dedup pass):

```sql
UPDATE chronicler.projection_checkpoints
SET watermark = NULL,
    watermark_id = NULL,
    updated_at = now()
WHERE source_name = 'google_calendar.completed'
  AND (subsource IS NULL OR subsource = '');
```

(The one-time `backfill_episode_participants.py` script was removed by bu-cfsgy
when the derived `episodes.entity_id` column it read was dropped.)

### Verify episode_entities is populated

```sql
-- Non-zero means the adapter has written participant rows.
SELECT COUNT(*) AS total_links FROM chronicler.episode_entities;

-- View column is non-NULL and returns arrays (empty or populated).
SELECT
    (participant_entity_ids IS NOT NULL) AS column_present,
    COUNT(*) AS n
FROM chronicler.v_episodes_corrected
GROUP BY 1;
```

### Calendar projection read grants

`CalendarCompletedAdapter` reads `calendar_event_instances` together with its
`calendar_events` and `calendar_sources` join companions.  When participant
links exist, it also reads `calendar_event_entities`, and optional owner
resolution reads `public.google_accounts`.  The restricted
`butler_chronicler_rw` role receives explicit `SELECT` grants for those four
calendar tables and the shared account lookup through `chronicler_026` and the
post-calendar core convergence migration `core_207`; an incomplete grant is
reported as an unavailable calendar read surface rather than allowing a
projection query to fail opaquely.  `calendar_event_entities` remains optional
for deployments that predate the calendar participant table.

### Derived column cleanup (bead bu-cfsgy, DONE)

The derived `episodes.entity_id` column (added by `chronicler_013`) was dropped
by migration `chronicler_016`. The owner-only single-column filter
(`list_episodes(entity_id=...)`, the `entity_id` MCP/API query param, and the
adapter/entity-merge writes) was removed at the same time. Callers filter by
entity via `participant_entity_id` (the `episode_entities` join, exposed as
`participant_entity_ids`).
