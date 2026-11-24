# Chronicler

> **Purpose:** Butler-specific Chronicler contracts that have no other home.
> **Audience:** Developers changing Chronicler adapters or editorial endpoints.
> **Prerequisites:** [Butlers](index.md), [Chronicler manifesto](../../roster/chronicler/MANIFESTO.md).

Moved verbatim from `AGENTS.md` (Butler-specific rules without a docs page). Identity and scope live in the manifesto.

- Chronicler adapters whose evidence is mirrored across schemas derive `source_ref` from the
  upstream identifier (`calendar:{origin_instance_ref}`), so the `(source_name, source_ref)` upsert
  dedupes the fan-out. `health.steps` and `health.heart_rate` are point-event sources with no lane
  mapping. `SpotifySessionAdapter` falls back to `track_names` when both context fields are NULL.
- Chronicler editorial endpoints (`/api/chronicler/briefing|attention|kpi`) already accept any
  `?date=&tz=` deterministically via `editorial.compose_briefing_payload`; date navigation is a
  frontend concern.
- The Chronicles page (`/chronicles`) also accepts `?episode=<episode-id>`, which opens the
  existing `EpisodeDrawer` for that exact episode over the selected day. Closing the drawer
  removes only `episode`; an unknown episode fails inside the drawer, and a date-only link is
  unchanged. In-page episode clicks use the same parameter. The entity page's Shared Chronicles
  rows link here.

### Owner-day composition

The bounded day-close bundle separates `owner_day.observations` from source
inventories and diagnostic counts. Its deterministic narration seeds use only
normal-privacy evidence in the exact owner-local day, retain readable timestamps,
confidence and machine source references, and select representative kinds before
the single interpretation call. Automated Butler sessions are processing records,
not owner conversations. Inbound email does not establish a human exchange;
actual owner outbound evidence supports sending a message, without inventing a
recipient or reply. Playback overlapping recorded sleep is background or
uncertain playback; playback alone never proves attentive listening. Unknown
places, movement destinations, travel modes and gaps remain explicit.

Raw episodes/events, per-source roll-ups and counts remain diagnostics. Their
producer classifications, time aggregates and editorial corrections are unchanged.
Derived observations share the existing character/byte budget and retain a raw
evidence anchor while present, so they cannot authorize an empty-day coverage
witness. Privacy masking precedes all derivation; sensitive sleep cannot qualify
normal playback by leaking its timing through this view. The existing masked
envelopes retain their separate limited narration rules.

The automatic session-episode caller excludes recognized delivery/tool-summary
bookkeeping only for the configured Chronicler's exact scheduled
`chronicler_day_close` trigger. It uses the cache classifier's existing scaffold
family without applying the cache's broader JSON/prose rules to memory. Session
results and audit records remain intact; routed corrections, other tasks,
other Butlers and genuine retrospective prose retain their existing memory path.
This hook is distinct from model-invoked memory write tools and deterministic
daily fact writeback; those keep their own admission and ownership rules.

### Late material and missingness

Day-close writeback distinguishes a supported numeric zero from an absent or
zero-filled rollup. A target lane needs an actual sample in the target local
day/timezone; its baseline contains only supported samples from the requested
window. Unavailable reads skip synthesis. Existing feeder-dark flags suppress
claims only for affected lanes on each sample's actual local date; target-day
availability cannot certify historical baseline days. Missing sleep cannot produce debt from baseline-only
samples. Cumulative debt also requires the complete supported requested calendar
window, so missing dates are never compressed into a shorter claimed interval.
The generic balance API retains its zero-filled response shape.

### Write-back integrity

- **Configured-absent sources are not outages.** An `optional_schema` source whose adapter skipped
  because its evidence surface is absent, and which has never projected successfully, does not
  raise `feeder_dark` (`flags.is_source_configured_absent`). It still gates the behavioral flags
  that depend on it, so a missing sleep feeder never produces `sleep_missing`. A source that fed
  the chronicler before and then went inactive, or any inactive required source, is still dark.
- **Retired sources are not outages, and availability is frozen per day.** A source declared
  retired in `contracts.INITIAL_SOURCES` (`retired_at`, seeded to `source_adapter_state`;
  `health.meals` since 2026-08-13) never raises `feeder_dark` from that date, though it still
  gates the behavioral flags that depend on it. `chronicler_rollup_daily` records each closed
  local day's source availability once, in the append-only `day_source_availability` table
  (`available | dark | configured_absent | retired`), on its first run after the day closes
  (within 36 hours). Flags read that record instead of current state, so a source that recovers
  no longer erases the days it was down. A closed day with no record past the horizon reports
  `historical_health_unknown` and its flags are left as they were. The feeder-dark flag detail
  lists `dark_sources`, `retired_sources` and `configured_absent_sources`. Each run also drops a
  retired source from past `feeder_dark` flags dated on or after its `retired_at` (idempotent;
  `corrected_by = 'retired_source'`); every correction, including a flag deleted because the
  retired source was its only dark one, is first logged in the append-only
  `retired_flag_corrections` with the original severity and detail. `day_source_availability` and
  `retired_flag_corrections` also refuse `TRUNCATE`. `daily_repair_days.availability_snapshot` is
  superseded by the table and unused.
- **Narration is gated per lane, not per day.** The once-daily labeling pass reads the frozen
  availability record. A lane is uncaptured when every source feeding it was dark, retired or
  configured-absent that day. The pass skips only for `no_rollup_data`, `all_lanes_dark` or
  `availability_unknown` (no snapshot); a `feeder_dark` flag alone no longer skips the day. The
  prompt lists `uncaptured_lanes` and forbids describing activity or its absence there, and
  `parse_narration_response` rejects a summary pairing a negation with a keyword of such a lane
  (`llm_output_invalid`). Re-narrating past days is not done here (bu-uhbhzq.12).
- **Day-close unknowns name uncaptured lanes.** `owner_day.unknowns` gains one line per
  uncaptured lane ("Meals not captured (source retired since 13 Aug)"), stored every day and
  carrying no source identity. `owner_day.recap_mention` lists the lines the delivered recap may
  mention: a dark lane on its first dark day and weekly after, a retired lane once. One SQL query reads the
  frozen days to compute a lane's streak in calendar days, with no cutoff; a day with no snapshot
  neither counts nor resets it. Both the `chronicler_day_close_bundle` tool and the day-close
  cache pass them, and the day-close prompt mentions a gap only through `recap_mention`.
- **Sleep debt is only "building" on a short night.** The closed day must itself fall short of the
  usual, so a long night never yields both "slept more than usual" and "debt building".
- **Confidence is evidence-derived.** `writeback.evidence_confidence` scales linearly from 0.4 to
  0.9 with baseline coverage (supported sample days over the lookback window). Self-reminders carry
  the 0.4 floor. The value is written to the facts `confidence` column through `store_fact`, not
  the blanket 1.0 default.
- **The day-close session writes no memory.** Its prompt forbids memory write tools; delivery and
  scheduling receipts (sent, deferred, not configured) are operational status, never facts.

The daemon owns one canonical historical-refresh service shared with the
existing dashboard control. The dashboard still refuses runtime-origin calls.
Only the daemon passes its private continuation object to scheduler dispatch;
job arguments cannot select a caller or collaborator. Projection and rollup jobs
remain deterministic. Their successful registered return precedes the scheduler
tail, which may resume one eligible silent refresh. An ordinary day-close in that
tick suppresses an additional refresh. No cron, narrator, notification surface or
memory writer is added; the existing hour-one recap and opted-in interview retain
their separate behavior.

Own-schema `state` records opaque narration-visible revisions, pending outcomes
and a fair closed-day cursor. Discovery reads durable local tuples, so restart or
a missed in-memory hook does not lose an update or deletion. Volatile rollup
computation timestamps are not revisions. Only the accepted privacy projection
enters a digest; raw material is never stored in continuation state. A checkpoint
or empty rollup is not source-completeness or quiet-day proof. Only the existing
canonical writer's current admission, successful witness write and durable
readback can establish coverage. An older authoritative row alone cannot prove
this execution's persistence; idempotent successful writes need not advance its
timestamp. A newly discovered empty
projection without lawful visible material or an authoritative prior witness
remains pending; zero-fill alone never starts an automatic quiet refresh.
A changed revision is marked processed only after that outcome and a second
material read agree. Ambiguous, invalid, unavailable and cancelled outcomes stay
pending. Successful quiet refreshes share the existing 24-hour rate limit. The
existing enabled task's token budget and the 100-second absolute operation bound,
including lock wait, also apply to this caller.

Timeout and cancellation retain the whole operation and native asyncpg holder
release. Shutdown stops intake and uses its existing finite cleanup budget. An
incomplete release quarantines the physical pool and dependent modules; retained
cleanup closes them once settlement completes. It never reports success while a
release is incomplete or closes the shared pool underneath retained work.
Local software controls do not establish real PostgreSQL, model/provider or
owner-delivery outcomes; those require their source-bound hosted evidence.

### Where the owner was: anchors and situational blocks (bu-r3fmja)

OwnTracks reports sparsely and goes silent while the phone is still, so place clustering bridges a
silent gap of up to 16 h at the same place, and a silent stay ends at the departure fix
(`owntracks_place_cluster.py`, REQ-butler-chronicler-014). `place_episode` rows are `evidence`:
they say where the owner was and never count on their own.

`chronicler.situation_inferred` (`adapters/situation.py`, pure rules in `situation.py`) turns those
stays into counted blocks every 30 minutes, re-deriving the last 3 owner-local days:

- An **anchor** decides the lane (`anchors.py`). Per kind, the first source with one wins: the owner
  place registry (`public.owner_places`), then the memory graph, then `OWNTRACKS_PLACE_REFERENCES`,
  then (home only) the place most often occupied at 03:00 over 28 days. Work is never inferred.
- In the **graph**, work is the geocoded `has-address` of the organisation the owner `works-at`;
  home is the owner's own `has-address`, else the address of the place they `rental-location` /
  `lives-at`. Relationship geocodes those addresses into `relationship.address_geocodes`; a geocode
  counts only while its hash matches the address text. To register work, tell Relationship
  "I work at <Org>; <Org>'s address is <address>".
- **Blocks:** work anchor → `work_presence_block` (Work), home → `home_block` (Rest), airport →
  `airport_block` (Travel), anywhere else within 50 km of home → `outing_block` (Social). Farther
  away is a trip and gets no block.
- **Precedence:** each block is its stay minus every higher-ranked lane's activity (sleep >
  exercise > travel > work > play > eat > social > rest) and every owner gap-interview answer.
  Steam at home is Play; the rest of the evening is Rest. Butler sessions, comms bursts, Spotify and
  episodes under 10 minutes never preempt; pieces under 15 minutes are dropped.
- A **provisional hold** covers the stay the clusterer has not closed yet, from the trailing run of
  same-place fixes to the earliest of last fix + 12 h and now.
- Each run tombstones the day's earlier blocks it no longer derives, except owner-corrected ones.
  Payloads carry the anchor kind, label and source and a distance from home rounded to 100 m; no
  coordinates.

The day-close gap interview (`gap_interview.py`) asks about the longest unexplained waking block
(at least 30 minutes) when no occupation block anchors the day, e.g. "Yesterday 07:55–10:40 isn't
accounted for (about 18 km from home). What were you doing?". The Work / Social / Play / Eat / Rest
buttons write one `chronicler.owner_answer` activity block in that lane; Dismiss counts nothing.

### Travel intents and location (bu-2tsgqb.7)

`reconcile_day` checks one-way travel intents (a flight, or a route block `A -> B`, 30 min to 24 h)
against accepted OwnTracks fixes (`src/butlers/chronicler/travel_location.py`). Same place before
and after the window drops the intent with `evidence="location"`, but only for an intent that
expects a long trip (explicit flight wording in the title); a
route without flight wording, like `Home -> Office` or `SIN -> Aomori`, stays `unknown`. 300 km or more at a plausible
speed keeps it with a `consistent` note that states only the displacement, never that the trip was
taken; anything else is `unknown`. Each anchor needs two fixes; `trail_outlier`, coarse (>1 km) and
naive-timestamp fixes are ignored. There is no geocoding, so an event's stated place is never
checked. Every intent also carries `reconciliation.plan_producer`: `butler` for events the calendar
module marked `butler_generated` (or titled `BUTLER:`), else `calendar`.

### Timed Steam Play and music evidence (bu-r3fmja.7)

The Steam connector persists detected `personastate` / `gameextrainfo` changes in
`connectors.steam_status_history` alongside ingest submission. `gameid` is stored
but never changes that delta rule. A storage failure still advances the polling
cursor and degrades health. Chronicler has SELECT only; connector writes retain
the existing connector role boundary. Bootstrap replay preserves the read grant.

`steam.status_history` projects minute-precision `play_session` evidence every
five minutes. A game ends at the next no-game, other-game or offline change.
Same-game persona changes keep it open. Open sessions are provisional, capped
at twelve hours, and refreshed on replay using the same source identity. The
adapter scans sparse history in bounded keyset pages, carrying an active game
across page boundaries; its checkpoint does not hide an open session.

Situational fusion alone emits counted `play_block` rows. Known away-from-home
where-segments, higher-precedence activity and owner answers cut Play out;
unknown location allows it. Only accepted Play displaces home time. A local date
with any status session suppresses the daily-total guess; dates with none use
the existing daily-total placed window under the same exclusions. Steam daily
totals and Spotify listening rows are evidence, including historical rows.
Music has no Play lane, but remains available as a quiet-run breaker.
The migration resets the situation checkpoint once to rebuild historical fallback
Play. Later runs also revisit dates of changed Steam evidence, so late session
closures can replace older provisional blocks without losing old daily totals.
Tombstones and owner corrections revisit canonical, corrected and previously
projected provenance dates, removing stale counted Play after evidence moves.

The additive core table and historical evidence survive source rollback;
downgrade does not destroy observations or reintroduce directly counted source
rows. Trip-span fusion belongs to `bu-r3fmja.8`, which composes its exclusions
with this source; this change does not infer trips from calendar or PC power.

## Projection operations and migration verification

**Ops verification post-deploy:** after one cron tick, run
```sql
SELECT payload->>'trigger_source' AS trigger_source, count(*)
FROM chronicler.episodes
WHERE source_name = 'core.sessions'
  AND start_at > now() - interval '10 minutes'
GROUP BY 1;
```
`tick`, `qa`, `healing`, and any `schedule:*` values must not appear.
`deadline:*` values MAY appear and are expected in the Tasks lane.

### Ops sessions escape hatch

> **Canonical:** the projection-exclusion + ops-endpoint contract is normative
> in the spec, see `openspec/specs/butler-chronicler/spec.md` Requirement
> "Operational Sessions Excluded from User-Visible Projection". Keep these
> implementation notes consistent with the spec.

Operational sessions are **never projected** into `chronicler.episodes`.
Engineers who need to audit scheduler cadence, switchboard tick rate, or QA
canary health can use the dedicated ops endpoint, which reads the raw sessions
tables directly via `fan_out_with_status`.

**Endpoint:** `GET /api/chronicler/ops/sessions`

**Purpose:** Returns only the sessions whose `trigger_source` matches the
exclusion set (`tick`, `qa`, `healing`, `schedule:*`). These are invisible to
the user-facing `/api/chronicler/episodes` endpoint by design.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `trigger_source` | string (optional) | Filter to a specific ops source (e.g. `tick`, `schedule:chronicler_day_close`) |
| `since` | ISO datetime (optional) | `started_at >= since` |
| `until` | ISO datetime (optional) | `started_at < until` |
| `limit` | int 1–500 (default 50) | Max rows returned |

**Example (last 50 tick sessions across all butlers):**
```bash
curl 'http://localhost:8000/api/chronicler/ops/sessions?trigger_source=tick&limit=50'
```

**Example (all schedule:* sessions in the last hour):**
```bash
curl "http://localhost:8000/api/chronicler/ops/sessions?since=$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)"
```

**Response shape:**
```json
{
  "data": [
    {
      "butler": "chronicler",
      "session_id": "...",
      "trigger_source": "tick",
      "started_at": "2026-04-29T10:00:00Z",
      "completed_at": "2026-04-29T10:00:01Z",
      "duration_ms": 1234,
      "success": true,
      "model": "claude-sonnet-4-6"
    }
  ],
  "meta": {"total": 1, "offset": 0, "limit": 50}
}
```

**Invariant:** data from this endpoint will NEVER appear in
`/api/chronicler/episodes`. The separation is enforced at the adapter layer
(`CoreSessionsAdapter`) and tested in `tests/chronicler/test_ops_sessions_api.py`.

### Frontend timezone source (bu-k18cm)

All timestamps in the Chronicles frontend (Gantt, Scrubber, EpisodeDrawer,
axis tick labels) render in the **owner's configured timezone**, not the
browser's local timezone.

**Source:** `GET /api/settings/general` → `data.timezone` (IANA name, e.g. `"Asia/Singapore"`).

**Fallback:** `"Asia/Singapore"` (matches the `SGT` constant in `briefing.py`),
used while the API call is in-flight or returns an error.

**Implementation:**
- `App.tsx` fetches the timezone via `useGeneralSettings()` and mounts one
  `<AppTimezoneProvider timezone={ownerTz}>` around all routes.
- `ChroniclesPage` reads `ownerTz` via `useTimezone()`, derives the latest
  settled owner-local day with `dayKeyInTimeZone()` / `shiftDayKey()`, includes
  `tz: ownerTz` in briefing and day-close refresh requests, and passes
  `{date, tz}` to `ChroniclesDrilldownPanel`; it does not fetch the setting or
  mount another provider.
- `ChroniclesDrilldownPanel` derives the selected day's `[from, to)` UTC window
  with `dayWindowInTz(date, tz)` from `frontend/src/lib/tz-format.ts`.
- Child components read the timezone directly via `useTimezone()` from
  `frontend/src/components/ui/timezone-context.tsx`.
- All formatting uses `date-fns-tz` (`formatInTimeZone`, `fromZonedTime`); never
  `Date.toLocaleString` or `Date.toLocaleTimeString`.

### Heartbeat tombstone migration verification (chronicler_007 / bu-6t63s)

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

### Memory calendar episode tombstone migration (chronicler_008 / bu-aqqx0)

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

### Session title re-projection watermark reset (chronicler_009 / bu-jpf3o)

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

### Backfilling episode_entities on historical episodes (bu-xuqyo)

Migration `chronicler_014` added the `episode_entities` join table to `chronicler`.
Episodes projected **before** the adapter change (bu-3zve1, PR #1869) have no rows
in `episode_entities`.

> **Note (bu-cfsgy):** the derived `episodes.entity_id` column and its one-time
> backfill scripts (`backfill_episode_entity_id.py`,
> `backfill_ha_presence_entity_id.py`, `backfill_episode_participants.py`) were
> removed when the column was dropped (migration `chronicler_016`). The owner
> entity now lives **only** in `episode_entities` (role='owner'), written at
> projection time. To repopulate historical rows, use the watermark reset below.

#### Watermark reset (full re-projection)

Reset the adapter watermark to `NULL` so the next scheduled run re-projects all
calendar episodes from scratch.  Each row will receive `episode_entities` rows at
projection time (re-writes titles and re-runs the dedup pass).

```sql
UPDATE chronicler.projection_checkpoints
SET watermark = NULL,
    watermark_id = NULL,
    updated_at = now()
WHERE source_name = 'google_calendar.completed';
```

After the reset, trigger a manual adapter run or wait for the next scheduled
invocation.

#### Verify backfill completeness

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

### Migration 014: episode_entities join table (bu-t0130)

Migration `chronicler_014` (`roster/chronicler/migrations/014_episode_entities.py`)
adds multi-entity support to chronicler episodes.

#### Pre-condition

`chronicler_013` must be applied first (it created the original
`v_episodes_corrected` shape that 014 extends). Note: the derived
`episodes.entity_id` column 013 added was later dropped by `chronicler_016`
(bu-cfsgy); the owner entity now lives in `episode_entities`.

#### Post-conditions

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

#### Role-precedence collapse rule

Writers (`CalendarCompletedAdapter`) MUST collapse multiple roles for the same
`(episode_id, entity_id)` pair before writing, keeping the highest-precedence
role:

```
'owner' > 'organizer' > 'participant'
```

Because the PK is `(episode_id, entity_id)`, each `entity_id` appears at most
once per episode.  An attendee who is also the calendar account owner is written
exactly once with `role='owner'`.

#### Backfilling episode_entities (bu-xuqyo)

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

#### Verify episode_entities is populated

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

#### Calendar projection read grants

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

#### Derived column cleanup (bead bu-cfsgy, DONE)

The derived `episodes.entity_id` column (added by `chronicler_013`) was dropped
by migration `chronicler_016`. The owner-only single-column filter
(`list_episodes(entity_id=...)`, the `entity_id` MCP/API query param, and the
adapter/entity-merge writes) was removed at the same time. Callers filter by
entity via `participant_entity_id` (the `episode_entities` join, exposed as
`participant_entity_ids`).

### Grafana panel proposal: participant resolution telemetry (bu-qlce5, PR #1871)

NOTE: This is a documentation-only proposal. No Grafana dashboard JSON is wired in this
bead. A follow-up bead can wire the actual panel.

#### Proposed panel 1: Adapter participant resolution rate

- **Metric:** `rate(chronicler_episode_participants_resolved_total[5m])` grouped by `schema`
- **Type:** Time-series
- **Why useful:** Shows how many participant `episode_entities` rows the calendar adapter
  resolves per second, broken down by butler schema. A sustained rate of zero for a schema
  that is expected to have meeting attendees indicates either the backfill (`bu-xuqyo`) has
  not run yet, or `calendar_event_entities` is missing for that schema.

#### Proposed panel 2: Episode list filter_kind request rate

- **Metric:** `rate(otelcol_receiver_accepted_spans_total[5m])` filtered on span attribute
  `chronicler.episodes.filter_kind` (values: `participant_join`, `owner_only`, `none`)
- **Type:** Time-series or stat panel
- **Why useful:** Compares the rate of API calls using `?participant_entity_id=` (participant_join)
  against `?entity_id=` (owner_only) and bare unfiltered calls (none). A flat-zero rate for
  `participant_join` while the adapter counter is non-zero means the multi-entity feed is not
  yet being consumed by any client: either the frontend integration is pending or callers are
  still using the owner-only filter.

#### Gap insight

The gap between `chronicler_episode_participants_resolved_total` (adapter writes) and the
`participant_join` request rate (API filter usage) is the key signal:

- **High adapter rate + zero participant_join calls:** backfill is running but no client
  consumes multi-entity episodes yet.
- **High both:** integration is live.
- **Zero adapter rate:** `calendar_event_entities` is absent on all schemas or the adapter
  has not run since bu-3zve1 (PR #1869) was deployed.
