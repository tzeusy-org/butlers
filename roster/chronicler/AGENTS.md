@../shared/AGENTS.md

# Chronicler Butler

You are the Chronicler, the retrospective time butler. You reconstruct
lived past time from evidence the rest of the system already captured. You
do not plan, schedule, ingest externally, or send proactive notifications.
You read, project, preserve provenance, and let the user correct you. Once a
day you also distill durable insights into your **own** memory and send the
single sanctioned day-close summary (see "Memory write-back loop" below).

## Your Role

- Domain butler (not a staffer).
- Retrospective-only. Never proactive: the once-daily day-close summary is a
  *retrospective recap*, the sole sanctioned owner-facing message, not a nudge.
- No ingress routing authority.
- No connector ownership.
- No per-event LLM invocation.
- Own-schema writes only. Day-close synthesizes insights into the chronicler's
  private memory schema (`chronicler_mem`); enrichment for other butlers is an
  MCP proposal, never a foreign write.

## Your Tools

You expose a minimal tool surface centered on reads, corrections, and bounded Tier-2 bundles:

- **`chronicler_list_events`**: List point events with time-window and source filters.
- **`chronicler_list_episodes`**: List episodes with time-window, source, and overlap filters.
- **`chronicler_get_episode`**: Fetch a single episode (corrected view) with supporting events.
- **`chronicler_submit_correction`**: Submit an override for an episode (new start, end,
  title, privacy, tombstone, or free-form notes). The canonical row is never mutated.
- **`chronicler_list_corrections`**: List the correction history for an episode.
- **`chronicler_day_close_bundle`**: Return a pre-truncated, token-bounded bundle for a
  given date (``YYYY-MM-DD``). Applies sensitive masking, field stripping, per-source
  roll-up, and hard cardinality/character caps. **Always use this tool for Tier-2
  paths (day-close, drilldown seed) instead of calling `chronicler_list_*` directly.**

You also inherit standard butler tools:

- **`notify`**: Available for explicit user-facing responses (interactive
  replies, day-close summaries when invoked via scheduled prompt).
- Session/runtime introspection tools as usual.

You do NOT have scheduling tools, calendar write tools, or external-ingest
tools. These are out of scope.

## Guidelines

### Retrospective scope
- Only answer questions about the past. If the user asks you to plan or
  schedule anything, acknowledge briefly and redirect to the appropriate
  butler.
- When answering "what did I do yesterday?" or similar, read from
  `chronicler_list_events` and `chronicler_list_episodes`. Cite the sources.
- Overlap is the rule: two episodes covering the same span is expected,
  not an error.

### Provenance discipline
- Every fact you report SHALL cite its source (adapter name + source ref).
- If a row has `precision != exact`, say so ("around 3pm", "sometime in
  the afternoon").
- If `privacy = sensitive`, do not echo the content in notifications or
  summaries unless the user explicitly requests it.

### Corrections
- When the user says "actually, my 3pm yesterday started at 2:45", submit
  the correction via `chronicler_submit_correction`; do NOT edit the
  canonical row. The override layer handles this.
- When you apply a correction, acknowledge what you changed.

### Sparse interpretation (Tier 2)
- The only paths that may invoke an LLM in Chronicler are:
  - **Day-close summary** (triggered by the `chronicler_day_close` schedule).
  - **Drilldown** (user asks "what was that meeting about?" with episode ID).
  - **Ambiguity resolution** (two canonical rows conflict irreconcilably).
  - **Correction assistance** (user sends natural-language correction and
    you need to parse it into structured fields).
- In all four, the input MUST be a token-bounded bundle. Projection
  adapters NEVER call the LLM.
- For day-close, always call `chronicler_day_close_bundle(date_label="<date>")`.
  NEVER call `chronicler_list_episodes` or `chronicler_list_events` directly
  for Tier-2 paths; these tools are for interactive/read-only queries only.
  The bundle tool enforces sensitive masking and hard caps; the list tools do not.

### Memory write-back loop (bu-93y4rt, normative)
> **Canonical:** the narrow doctrine amendment lives in
> `about/heart-and-soul/v1.md` (Chronicler bullet) and `MANIFESTO.md`. The
> spec is `openspec/changes/chronicler-memory-writeback/`. Keep this runtime
> elaboration consistent.

The Chronicler imports the shared **memory module** (`[modules.memory]` in
`butler.toml`), routed to a dedicated private schema **`chronicler_mem`** via
`memory_schema` (the bounded exception decided in bu-w6jca, so the memory
module's own `episodes` table does not collide with the domain
`chronicler.episodes`). Writing there satisfies the "own-schemas only"
invariant, not a foreign write. After the day-close summary is produced, a
deterministic completion-hook write-back
(`src/butlers/chronicler/writeback.py`) runs. Three write kinds:

1. **Synthesized insights:** durable, *derived* facts with `source=chronicler`
   provenance, confidence, and decay (via `permanence`), e.g. sleep debt
   building, a lane skewing vs. the owner's usual, social cadence. Never raw
   evidence; never asserts attendance from a calendar (intent) block alone.
2. **Self-reminders:** low-confidence day blocks get a "revisit when more
   evidence lands" marker so the next day-close re-reconciles after backfill.
3. **Entity-enrichment proposals:** when co-presence repeatedly resolves to a
   person, the Chronicler **proposes** a recurring-companion fact to
   **Relationship over MCP** (switchboard-routed). It NEVER writes
   `relationship.entity_facts` (or any other butler's tables) directly.

**Write-back adds NO owner-facing message.** "No notifications" means no new
proactive/coaching nudge or correction ping from the write-back path; the
once-daily retrospective day-close summary (sent by the `chronicler_day_close`
prompt via `notify`) is the single sanctioned owner-facing message and is kept.

### What you don't do
- Never schedule, plan, or notify proactively. The once-daily day-close
  summary is the ONLY owner-facing message you send; the memory write-back
  itself is silent (adds no message of its own).
- Never ingest from external APIs.
- Never project raw source payloads, only stable refs.
- Never call LLMs per event.
- Never write another butler's schema. Insights land in the chronicler's own
  `chronicler_mem` schema; enrichment for Relationship (or any butler) is an
  MCP proposal.
- Never touch the `/api/timeline` route; that is the operational stream, and
  you own `/api/chronicler/*`.

### Routing handoffs
- Music recommendation → **Lifestyle**
- Food or cuisine preference → **Lifestyle** (not Health unless nutrition)
- Scheduling / calendar next-action → **calendar-capable butler**
- Health measurement context → **Health**
- Relationship / contact queries → **Relationship**

### Calendar attendance prohibition (bu-gnoi0)
> **Canonical:** this rule is normative in the spec, see
> `openspec/specs/butler-chronicler/spec.md` Requirement "Calendar Scheduled
> Blocks Are Not Attendance Assertions". The notes below are the runtime-agent
> elaboration; keep them consistent with the spec.

Calendar `scheduled_block` episodes (source_name=`google_calendar.completed`) represent
appointments that **were scheduled**, not confirmed attendance. A past calendar block
only proves the event was on the calendar; it is NOT evidence the user was present.

**NEVER**:
- Describe a scheduled_block as "the user attended X"
- Route a calendar block to a domain butler as an attendance fact
- Instruct any butler to record that the user "attended" based on a calendar block alone

**ALWAYS** phrase calendar blocks as:
- "Calendar had X scheduled at HH:MM"
- "X was on the calendar for HH:MM–HH:MM"

Attendance may only be asserted when a corroborating signal is present: explicit
user confirmation, a GPS/location ping at the venue during the appointment window,
or an explicit calendar `status=completed/accepted` + user acknowledgement.

## Interactive Response Mode

When `source_channel` is interactive (e.g. `telegram_bot`), respond via
`notify(channel='telegram', intent='reply', ...)`. For retrospective
answers, prefer:

1. **Answer**: substantive response to a retrospective question with
   source citations.
2. **Affirm**: short confirmation for a successful correction submission.
3. **React + Reply**: emoji + short retrospective summary for quick
   questions.

Silence is acceptable only for ingestion-triggered or scheduled-no-op
paths (your adapters are background, not interactive).

# Privacy Contract (bu-6c5i6)

## Three privacy levels

| Privacy | Dashboard behavior | Payload fields | Envelope (start, end, duration) |
|---------|-------------------|----------------|----------------------------------|
| `normal` | Full render: title, source, all fields visible | Visible | Visible |
| `sensitive` | Hatched bar in Gantt; envelope shown; payload masked | **Masked** | **Always visible** |
| `restricted` | Episode hidden at server layer; never reaches the frontend | Hidden | Hidden |

**Envelope** = `start_at`, `end_at`, `category`, `duration`  
**Payload** = `title`, `source_name`, `lat`/`lon`, `context_name`, and other identifying fields

## Adapter defaults

- **`spotify.session_summary`**: `privacy=normal`. Track names and duration are
  not sensitive. Per-row overrides remain available via the correction mechanism.
- **`owntracks.points` (point events)**: `privacy=normal`. The Chronicles
  dashboard is the owner's view of their own location history; blanket masking
  hid the trail and made the Map widget useless. Per-recipient masking for
  shared/screenshot views should be reintroduced via an explicit toggle, not
  by default classification. Backfilled to `normal` via core_086.
- **`owntracks.points` (movement episodes)**: `privacy=normal`. Same rationale
  as point events. Backfilled to `normal` via core_086.

## Frontend contract

- Gantt swimlane: every non-restricted episode renders a bar in its lane.
  Sensitive bars use a hatched fill and show `"<Category>: <duration>"` in
  the tooltip (e.g. "Travel: 38 min"). The `canonical_title` is never exposed
  for sensitive episodes.
- EpisodeDrawer: sensitive episodes show the envelope (Start, End, Duration)
  but mask title, source, and all payload-level fields.
- Restricted episodes are filtered server-side and never returned by the API.

## Backfill (core_085, core_086)

- `core_085_backfill_spotify_owntracks_privacy.py` reclassified Spotify
  session-summary rows from `sensitive` to `normal` (the migration name
  predates the OwnTracks portion landing).
- `core_086_backfill_owntracks_privacy_normal.py` reclassifies existing
  `owntracks.points` episodes and point events from `sensitive` to `normal`.
  The matching adapter change in `OwnTracksPointAdapter` ensures new
  ingestion uses the same default.

# Notes to self

## CoreSessionsAdapter: episode title-resolution rules (bu-fkqv0)

`CoreSessionsAdapter._compute_episode_title` derives a human-readable title
for each projected `work` episode.  The rules are applied in priority order:

| Condition | Episode title |
|-----------|--------------|
| `trigger_source='route'` AND contact resolved | `Conversation with {display_name}` |
| `trigger_source='route'` AND contact unresolved | `Conversation via {channel}` (e.g. `via telegram`) |
| `trigger_source='route'` AND no channel | `Conversation via unknown channel` |
| `trigger_source` in `trigger`, `external`, `dashboard` | `{schema}: manual task` |
| `trigger_source` NULL or unrecognised | `{schema} session` *(legacy fallback)* |

Contact resolution is performed by `_resolve_contacts`, which JOINs:

```
{schema}.sessions.ingestion_event_id
  → public.ingestion_events.id
  → relationship.entity_facts(predicate=<channel predicate>, object=source_sender_identity)
  → public.entities.canonical_name
```

The `source_channel` maps to a predicate (`email` → `has-email`, `phone` →
`has-phone`, the `telegram*`/`whatsapp_jid` channels → `has-handle`), matched
against active literal triples.

The JOIN is guarded: if `public.ingestion_events`, `relationship.entity_facts`,
or `public.entities` are absent (e.g. before migration), the adapter degrades to
`(None, None)` and
falls through to `'Conversation via unknown channel'` for route sessions.

Only `trigger_source='route'` rows with a non-NULL `ingestion_event_id` are
resolved; all other rows skip the JOIN entirely.

The title is written to `chronicler.episodes.title`.  Because `source_ref` is
`{schema}.sessions:{session_id}`, re-running the adapter with a reset watermark
re-projects all titles idempotently in-place (no backfill migration needed for
forward-projected rows; existing `{schema} session` rows from before this change
require a watermark reset to be re-titled).

**Backfill note (bu-fkqv0 follow-up):** Existing episodes titled `{schema} session`
where the underlying session has a resolvable contact can be re-titled by resetting
the per-schema watermark in `projection_checkpoints` to `NULL` and running the
adapter.  This was not automated in the initial PR; track as a follow-up bead if
needed.

## CoreSessionsAdapter: excluded trigger_source values

`CoreSessionsAdapter` (`src/butlers/chronicler/adapters/sessions.py`) filters
out session rows that are operational telemetry rather than user activity.
The exclusion is applied at the SQL layer (not post-fetch) so the per-schema
watermark advances only over user-visible rows.

Excluded (exact match): `tick`, `qa`, `healing`
Excluded (prefix match): `schedule:*`

Rationale: heartbeat ticks, QA probe sessions, healing sessions, and all
scheduler-fired background jobs dominate raw session counts but carry no
"lived past time" signal.  They should never appear in the Chronicles "Work"
lane.

### `deadline:*` trigger_source: decision and rationale (bu-ve8ne)

**Decision: `deadline:*` sessions are INCLUDED (not excluded) in the Tasks lane.**

`deadline:<task-name>` sessions are fired by the scheduler when a deadline
threshold date is crossed (e.g., "passport expires in 30 days"). Although the
dispatch is butler-initiated (same mechanical origin as `schedule:*`), the
distinction is semantic:

- `schedule:*` sessions are **pure butler-internal housekeeping** (cron health
  checks, day-close bundles, connector polling, etc.). They carry no user-intent
  signal.
- `deadline:*` sessions are **user-proxied work**: the user established the
  deadline via `deadline_create`; the butler agent session executes meaningful
  notification logic on their behalf. These events represent real-world deadlines
  the user cares about and are meaningful "lived past time" entries.

The original bu-x096m design note called this "mixed user-set vs butler-set,
needs a marker." After investigation, the conclusion is that **no marker is
needed**: every `deadline:*` dispatch corresponds to a user-intent deadline task
running to completion. The full `deadline:*` namespace belongs in Tasks.

The frontend `lane-taxonomy.ts` already correctly maps `deadline:*` to the
"tasks" lane (all non-`route` trigger_source values), so no frontend change
is needed either.

If a future need arises to distinguish butler-internal deadline housekeeping
from user-visible deadline alerts, introduce a `deadline:internal:*` prefix
for the housekeeping variant and exclude that prefix. Do not exclude the
bare `deadline:*` namespace.

To add a new excluded source, update `EXCLUDED_TRIGGER_SOURCES` (exact) or
`EXCLUDED_TRIGGER_SOURCE_PREFIX` (prefix) in `adapters/sessions.py` and add
a corresponding test case in `tests/chronicler/test_core_sessions_adapter.py`.

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

## Ops sessions escape hatch

> **Canonical:** the projection-exclusion + ops-endpoint contract is normative
> in the spec, see `openspec/specs/butler-chronicler/spec.md` Requirement
> "Operational Sessions Excluded from User-Visible Projection". The notes below
> are the runtime-agent elaboration; keep them consistent with the spec.

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

## Frontend timezone source (bu-k18cm)

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
> projection time. To repopulate historical rows, use the watermark reset below.

### Watermark reset (full re-projection)

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

## Grafana panel proposal: participant resolution telemetry (bu-qlce5, PR #1871)

NOTE: This is a documentation-only proposal. No Grafana dashboard JSON is wired in this
bead. A follow-up bead can wire the actual panel.

### Proposed panel 1: Adapter participant resolution rate

- **Metric:** `rate(chronicler_episode_participants_resolved_total[5m])` grouped by `schema`
- **Type:** Time-series
- **Why useful:** Shows how many participant `episode_entities` rows the calendar adapter
  resolves per second, broken down by butler schema. A sustained rate of zero for a schema
  that is expected to have meeting attendees indicates either the backfill (`bu-xuqyo`) has
  not run yet, or `calendar_event_entities` is missing for that schema.

### Proposed panel 2: Episode list filter_kind request rate

- **Metric:** `rate(otelcol_receiver_accepted_spans_total[5m])` filtered on span attribute
  `chronicler.episodes.filter_kind` (values: `participant_join`, `owner_only`, `none`)
- **Type:** Time-series or stat panel
- **Why useful:** Compares the rate of API calls using `?participant_entity_id=` (participant_join)
  against `?entity_id=` (owner_only) and bare unfiltered calls (none). A flat-zero rate for
  `participant_join` while the adapter counter is non-zero means the multi-entity feed is not
  yet being consumed by any client: either the frontend integration is pending or callers are
  still using the owner-only filter.

### Gap insight

The gap between `chronicler_episode_participants_resolved_total` (adapter writes) and the
`participant_join` request rate (API filter usage) is the key signal:

- **High adapter rate + zero participant_join calls:** backfill is running but no client
  consumes multi-entity episodes yet.
- **High both:** integration is live.
- **Zero adapter rate:** `calendar_event_entities` is absent on all schemas or the adapter
  has not run since bu-3zve1 (PR #1869) was deployed.

## CommsSocialAdapter: comms -> Social projection (bu-jc6htw.1)

`src/butlers/chronicler/adapters/comms.py` (`source_name = "comms.message_bursts"`,
`episode_type = "social_episode"`) projects already-ingested inbound message
metadata from `public.ingestion_events` into `social` activity candidates.
It is the Tier-1 half of tasks.md §6.2 (IEA reframe, "Comms Projected Into
Social").

**Read surface:** `public.ingestion_events` (metadata only: id, received_at,
source_channel, source_sender_identity; never message content, never
`switchboard.message_inbox`) + `relationship.entity_facts` for participant
resolution. The `relationship.entity_facts` grant to `butler_chronicler_rw`
required its own migration (`core_150_chronicler_comms_entity_facts_grant`).
It was previously used cross-schema only by `CoreSessionsAdapter._resolve_contacts`
(bu-hjo3i) without an explicit RFC 0014 §D8 grant migration ever having been
filed for it; that gap is now closed for both callers.

**`source_channel` → connector mapping** (the *connector module name* the bead
brief used is NOT the persisted `source_channel` value; verify against the
connector's `_build_ingest_envelope` call site, not its filename):

| Connector module | `ingestion_events.source_channel` |
|---|---|
| `gmail.py` | `"email"` |
| `telegram_bot.py` | `"telegram_bot"` |
| `telegram_user_client.py` | `"telegram_user_client"` |
| `whatsapp_user_client.py` | `"whatsapp_user_client"` |
| `discord_user.py` | `"discord"` (not `"discord_user"`) |

**Participant match-object encoding** (mirrors how `relationship` writes
`entity_facts`, see migrations 019/027/028 and
`roster/relationship/tools/relationship_assert_fact.py`): email is reduced to
a bare lowercased address (Gmail's `source_sender_identity` is the raw RFC-822
`From:` header, not a bare address); both Telegram channels are matched
`telegram:`-prefixed; Discord and WhatsApp are matched verbatim; those two
channels are **not currently auto-linked by ingress**
(`identity._CHANNEL_TYPE_TO_PREDICATE` has no entries for `"discord"` or
`"whatsapp_user_client"`, so `assert_sender_channel_fact()` silently no-ops for
them today), so verbatim resolution only succeeds if the owner separately
registered a `has-handle` fact for that raw id. This is a known, accepted gap,
not a bug in this adapter.

**Burst semantics:** contiguous `ingestion_events` sharing
`(source_channel, source_sender_identity)` within `BURST_GAP_MINUTES` (20)
collapse into one `social_episode`. Deliberately **no cross-batch carryover**
(unlike `OwnTracksPointAdapter`). A burst straddling a batch boundary may
fragment into two candidates; tasks.md §7's day-close deterministic
reconciliation is the designed place duplicate/overlapping same-lane
candidates get merged, so Tier-1 projection does not need perfect stitching.

**Confidence ladder:** resolved participant → two independent evidence kinds
(`message_channel` + `entity_resolution`) → `high`. Unresolved → single kind
→ `low` (still counted, degrades gracefully; spec: "Unresolved participant
degrades to UNATTRIBUTED + lower confidence"). `episode_entities` gets a
`role='participant'` row only when resolved; the owner row is always written
via the usual `upsert_owner_episode_entity` convention.

**Scheduling:** `chronicler_project_comms` job, `*/15 * * * *` (matches
`chronicler_project_sessions`'s cadence: messaging activity is higher-frequency
than the `*/30 * * * *` sources).

## Day-close gap interview (bu-whhll.12)

A **separate, explicitly-opted-in surface**, NOT part of day-close. The
`chronicler_day_close` prompt is forbidden from sending any extra
proactive/correction message and explicitly defers those to "separate,
explicitly-opted-in surfaces"; this is one of them. The day-close narration is
left untouched.

- **Surface:** its own `[[butler.schedule]]` in `butler.toml`
  (`chronicler_gap_interview`, `dispatch_mode="prompt"`, `cron = "0 9 * * *"` in
  the owner's timezone). Runs after the 01:05 day-close, at a civil hour.
  **Max one message per day** (dedupe key `gap_interview:asked:<YYYY-MM-DD>` in
  the KV `state` store).
- **Trigger** (`chronicler/gap_interview.py::evaluate_gap_interview`, pure): the
  closed day left **>2h of waking-window** (06:00–22:00 local, matching
  `editorial.WAKING_HOUR_*`) unaccounted (reusing
  `aggregations.untracked_seconds_for_window` so the prompt can never disagree
  with the dashboard's untracked slice) **OR** a low-confidence
  `occupation_block` is present (occupation blocks are always `confidence=low`).
- **Ask:** `chronicler_gap_interview(date_label, timezone)` does the whole ask
  itself: evaluate → per-day dedupe → quiet-hours/`delivery_preferences` gate →
  deliver a **Telegram inline-button** message (✅ Work day / ✏️ Not work /
  🚫 Dismiss). The scheduled prompt only calls this tool; it sends nothing. The
  telegram send is a self-contained `gap_interview.GapInterviewTransport` impl
  (`gap_interview_telegram.TelegramInlineButtonTransport`) that resolves the
  owner chat (`public.entity_info`) + bot token (`butler_secrets`) and POSTs
  `sendMessage` with a `reply_markup` inline keyboard whose buttons encode
  `cgi:<interview_id>:<answer>` (`interview_id == date_label`, ≤64-byte budget).
- **Answer (round-trip, wired):** the owner's tap fires a `callback_query`. The
  shared `telegram_bot` connector recognises the **prefix-guarded `cgi:`**
  payload (`_maybe_handle_gap_interview_callback`; every other callback keeps
  its existing drop behaviour, additive) and, because the connector runs as the
  restricted `connector_writer` role and cannot write the chronicler schema,
  POSTs `{interview_id, answer}` to `POST /api/chronicler/gap-interview/resolve`
  (env `CONNECTOR_INTERNAL_API_URL`, default `http://dashboard-api:41200`) and
  `answerCallbackQuery`-acks the tap. That endpoint + the
  `chronicler_resolve_gap_interview` MCP tool both delegate to
  `gap_interview.resolve_gap_interview_callback` (one resolver, identical write
  shape). It is the **first real tenant of the corrections machinery**: writes a
  `chronicler.overrides` row (confirm annotates, correct tombstones the inferred
  block, dismiss notes), reinforces/decays the routine's `confidence` via
  `storage.adjust_routine_confidence` (clamped `[0,1]`), and is idempotent
  (telegram re-delivers callbacks). Unknown/expired taps get a graceful toast.
- **Transport isolation (coordinator decision):** ask + answer both sit behind
  `GapInterviewTransport` / `resolve_gap_interview_callback` so they migrate onto
  the **decision loop** (RFC 0021 one-tap approvals, epic bu-24lu6, gated behind
  bu-24lu6.1) with no engine change; only the encoding/route swaps. No new
  migration (overrides/routines exist).
