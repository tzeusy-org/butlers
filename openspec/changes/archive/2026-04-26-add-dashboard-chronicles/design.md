# Design — Chronicles Dashboard Page

## Goals

1. Give the user a single retrospective surface that answers
   *"what was I up to, and where, at any point in time?"* — across
   overlapping concurrent activities, with aggregate breakdowns and
   spatial replay.
2. Reuse Chronicler-owned data exclusively (`/api/chronicler/*`).
3. Maintain the cost-discipline invariant: NO new LLM call paths on
   projection, aggregation, or page-render code.
4. Maintain the schema-isolation invariant: aggregate endpoints read
   ONLY from `chronicler.*` corrected views, never reach back into
   source butler schemas.
5. Degrade gracefully when projection adapters are PLANNED / DEFERRED:
   disabled-lane affordances driven by `/api/chronicler/source-state`.

## Non-Goals

- Replacing or repurposing `/api/timeline` (operational ops stream;
  preserved untouched per `chronicler-api/spec.md` L131–142).
- Building new projection adapters (OwnTracks, Steam, Google Health,
  Meals, Home Assistant). Each is a sibling unlock, not a child of this
  change.
- Storing a category / lane taxonomy in the `chronicler.episodes` table.
  RFC 0014 §D1 does not name it; we derive it deterministically on read.
- Real-time streaming. The page is retrospective by definition.
- Multi-user / multi-tenant features (out of v1 per `heart-and-soul/v1.md`).

## Architectural Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    /chronicles  (frontend)                  │
│ ┌──────────────────┐ ┌──────────────────┐ ┌───────────────┐ │
│ │  Time-window     │ │  Source-state    │ │ Auto-refresh  │ │
│ │  picker          │ │  badge strip     │ │ toggle        │ │
│ └──────────────────┘ └──────────────────┘ └───────────────┘ │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │  Gantt swimlane (recharts custom or visx-derived)       │ │
│ │  rows = categories; bars = episodes; tooltip = source   │ │
│ └─────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │  Map (maplibre-gl) + playhead bound to scrubber         │ │
│ │  trail = OwnTracks point events in window               │ │
│ └─────────────────────────────────────────────────────────┘ │
│ ┌──────────────────┐ ┌──────────────────┐ ┌───────────────┐ │
│ │ Pie: by-category │ │ Stacked bar:     │ │ Streak        │ │
│ │ (recharts)       │ │ by-day×category  │ │ callouts      │ │
│ └──────────────────┘ └──────────────────┘ └───────────────┘ │
│            ▲                  ▲                  ▲          │
└────────────┼──────────────────┼──────────────────┼──────────┘
             │                  │                  │
       /api/chronicler/   /api/chronicler/    /api/chronicler/
       episodes (existing) aggregate/by-category aggregate/by-day
                                  + by-day              + source-state
                                                        + events (existing)
             ▲                  ▲                  ▲
┌────────────┴──────────────────┴──────────────────┴──────────┐
│           roster/chronicler/api/router.py                   │
│  (extended with aggregate/* and source-state endpoints)     │
└─────────────────────────────────────────────────────────────┘
             ▲                  ▲                  ▲
┌────────────┴──────────────────┴──────────────────┴──────────┐
│  src/butlers/chronicler/aggregations.py  (new module)        │
│  - by_category(start, end, privacy_tier, include_tombstoned) │
│  - by_day(start, end, category, tz, ...)                    │
│  All queries: chronicler.v_episodes_corrected ONLY.          │
└─────────────────────────────────────────────────────────────┘
```

## Category Taxonomy

Two-tier mapping. Backend commits to a stable category string; frontend
owns visual presentation.

### D1: Backend `category_for(source_name, episode_type) → category`

Pure deterministic function, NO LLM. Returns one of:
`{ work, calendar, music, gaming, travel, sleep, meal, home, other }`.

Initial mapping (mirrors `src/butlers/chronicler/contracts.py`
declarations):

| `source_name` | `episode_type` | `category` |
|---|---|---|
| `core.sessions` | `work` | `work` |
| `google_calendar.completed` | `scheduled_block` | `calendar` |
| `spotify.session_summary` | `listening_episode` | `music` |
| `steam.play_history` | `play_episode` | `gaming` |
| `owntracks.points` | `movement_episode` | `travel` |
| `google_health.measurements` | `sleep_episode` | `sleep` |
| `health.meals` | `eating_event` (point) | `meal` |
| `home_assistant.history` | `presence_episode` | `home` |
| anything else | * | `other` |

Function lives in `src/butlers/chronicler/aggregations.py`. Returned as
the `category` field on every aggregate bucket record AND on episode
records (computed projection, not stored). A guardrail test asserts the
function imports nothing from `anthropic`, `openai`, `claude_agent_sdk`,
or `interpret`.

Adding a new category requires updating the function, the spec table
above, the frontend `LANE_TAXONOMY`, AND a unit test asserting every
non-`other` `(source_name, episode_type)` projected by an active adapter
maps to a non-`other` category.

### D2: Frontend `LANE_TAXONOMY`

Lives in `frontend/src/components/chronicles/lane-taxonomy.ts`. Maps
`category → { label, colour, icon, sortOrder }`. Source of truth for
visual presentation. Backend never returns colours or labels.

## Aggregation Endpoints

### D3: `GET /api/chronicler/aggregate/by-category`

Request:
- `start_at` (ISO8601, required)
- `end_at` (ISO8601, required, > `start_at`)
- `privacy_tier` (optional; default = exclude `restricted`; `sensitive`
  episodes counted by duration but never expose title/payload)
- `include_tombstoned` (default `false`)
- `tz` (IANA timezone, default `UTC`)

Response (`ApiResponse<CategoryBuckets>`):
```json
{
  "data": {
    "start_at": "...",
    "end_at": "...",
    "tz": "...",
    "buckets": [
      {
        "category": "work",
        "total_seconds": 28800,
        "episode_count": 14,
        "source_breakdown": [
          { "source_name": "core.sessions", "total_seconds": 28800,
            "episode_count": 14 }
        ]
      },
      ...
    ],
    "stale_caches": []
  }
}
```

Computation: SQL over `chronicler.v_episodes_corrected`. Each episode's
duration is `LEAST(end_at, query_end) - GREATEST(start_at, query_start)`,
clamped at zero. Open episodes (`end_at IS NULL`) are clipped to
`query_end` if started in window. Buckets are sorted by `total_seconds
DESC` then `category ASC` for deterministic ordering.

### D4: `GET /api/chronicler/aggregate/by-day`

Request: same as `by-category` plus optional `category` filter.

Response: list of `{day, category, total_seconds, episode_count}` rows
sorted by `(day ASC, category ASC)`. Day boundary follows the requested
`tz`.

### D5: `GET /api/chronicler/source-state`

Response (`ApiResponse<SourceStateRow[]>`): list of all
`source_adapter_state` rows with fields `source_name`,
`chronicler_compatibility`, `read_surface`, `boundary_semantics`,
`active`, `inactive_reason`, `last_run_at` (from
`projection_checkpoints`), `last_error` (from
`projection_checkpoints`), `optional_schema`. Read-only; no mutation.

### D6: Error Shape

Invalid time range (`end_at <= start_at`, missing required field, bad
`tz` string) → `400 Bad Request` with `ErrorResponse` envelope and
`code` in `{invalid_time_range, invalid_timezone, missing_parameter}`.
Mirror of `chronicler-api/spec.md` L124–129 ("Invalid correction
rejected"). NO partial bucket records returned.

### D7: Performance

- Index review: confirm `(source_name, start_at, end_at)` and
  `(start_at, end_at)` cover the corrected-view query plans.
- Latency target: P95 < 200 ms for a 7-day window with current SUPPORTED
  source set on synthetic fixture data; benchmark before merge per
  `craft-and-care/performance-discipline.md` measure-before-optimize.
- Cache layer: NOT introduced in this change. Aggregation endpoints are
  cheap enough on corrected views; revisit only if benchmark fails.

## Day-Close Tier-2 Cache Invalidation

### D8: Cache Storage

- New table `chronicler.tier2_cache` (single migration; nullable until
  needed) with columns:
  - `cache_key` (TEXT PK; format
    `day_close:{YYYY-MM-DD}` for window-keyed entries)
  - `start_at`, `end_at` (TIMESTAMPTZ, the window covered)
  - `cache_built_at` (TIMESTAMPTZ)
  - `prose` (TEXT, the Tier-2 output)
  - `provenance_refs` (JSONB, list of source_ref tuples cited by the
    prose for forensic correction tracing)
  - `superseded_at` (TIMESTAMPTZ, nullable)

### D9: Invalidation Rule

On read (`GET /api/chronicler/episodes/{id}/day-close-summary` —
NOT in MVP scope; the page reads cached prose via the page-level
endpoint described in §D10):

A cache entry is **stale** if any of the following holds for any row
within `[start_at, end_at]`:

- `chronicler.episodes.tombstone_at > cache_built_at`
- `chronicler.episodes.updated_at > cache_built_at`
- `chronicler.point_events.tombstone_at > cache_built_at`
- `chronicler.point_events.updated_at > cache_built_at`
- `chronicler.overrides.created_at > cache_built_at`

Stale entries are NOT returned — the response is `{ stale: true,
cache_built_at, last_invalidating_event_at }` and the page renders the
"summary out of date" affordance.

### D10: Page-Level Day-Close Read Endpoint

`GET /api/chronicler/aggregate/day-close?date=YYYY-MM-DD&tz=...` returns
either the cached prose with provenance refs OR the stale marker. NO LLM
invocation.

The user-clicked "regenerate" button POSTs to
`POST /api/chronicler/aggregate/day-close/refresh` with body
`{date, tz}`. This re-invokes the existing scheduled
`chronicler_day_close` Tier-2 path (NOT a new LLM path), rate-limited to
1 per day per window per cost discipline. On rate-limit breach the
endpoint responds `429 Too Many Requests` with the existing
`ErrorResponse` envelope (`code: day_close_rate_limited`,
`details.retry_after_seconds`).

## Map Render Contract

### D11: Privacy / Retention

- Default API parameters: `include_tombstoned=false`,
  `privacy_tier=normal,sensitive` (omit `restricted`).
- `restricted` episodes / events: excluded from both Gantt and map.
- `sensitive` episodes / events: rendered on the Gantt as masked entries
  (generic label, no `payload` exposure); coordinates NOT plotted on
  the map.
- Retention enforcement is upstream — the projection adapter and storage
  layer drop expired rows per source contract (OwnTracks default 30 d
  per `security.md` L172–175). The map widget receives only data that
  has already passed retention gates.

### D12: Playhead Binding

- Single time-scrubber controls both Gantt cursor and map playhead.
- Playhead position snaps to the nearest OwnTracks point event in
  window (no smoothing in v1).
- Lane brushing on Gantt also re-centers map (e.g. clicking a calendar
  episode pans map to its location if the underlying calendar event
  has a `location` field — this comes from the source butler, not from
  Chronicler).

## Disabled-Lane Affordances

### D13: From `source-state`

The page calls `/api/chronicler/source-state` once on load.

- `chronicler_compatibility = supported` AND `active = true` → lane
  enabled.
- `chronicler_compatibility = supported` AND `active = false` → lane
  enabled but rendered with a yellow "no recent data" banner; tooltip
  shows `inactive_reason` and `last_error`.
- `chronicler_compatibility = planned` → lane shown disabled with
  tooltip "Adapter planned; not yet implemented." Link to relevant beads
  epic if any.
- `chronicler_compatibility = deferred` → lane hidden by default;
  optional toggle to show.
- `chronicler_compatibility = not_time_bearing` → never shown.

## Auto-Refresh

### D14: `useAutoRefresh` Adoption

- "Today" window (where `end_at` is now-ish): 30 s default, options
  10/30/60 s, manual override available.
- Older windows: no polling. Manual refresh button.
- Hook is the existing `frontend/src/hooks/use-auto-refresh.ts`. No new
  defaults coined.

## Sidebar Placement

### D15: Nav Config

- Section: **Dedicated Butlers** (NOT Telemetry). Placement after
  Calendar, before Memory.
- Tooltip discriminator:
  - `/timeline` → "Live cross-butler operational stream (sessions,
    notifications, errors)."
  - `/chronicles` → "Retrospective lived-time reconstruction."
- File: `frontend/src/components/layout/nav-config.ts`.

## Invariants and Guardrails

### D16: No-LLM Page Invariant

- Aggregate endpoints, source-state endpoint, and page-render code MUST
  NOT import `anthropic`, `openai`, `claude_agent_sdk`, or
  `butlers.chronicler.interpretation`.
- A guardrail test under `tests/chronicler/test_aggregation_no_llm.py`
  scans new handler files for forbidden imports/identifiers.

### D17: No-Cross-Schema Invariant (resolution-based)

- All SQL issued by aggregation handlers and the source-state handler
  references only relations that resolve to the `chronicler` schema.
- The invariant is **resolution-based, not lexical**: bare table names
  (e.g. `v_episodes_corrected`) are accepted because the
  `butler_chronicler_rw` role's `search_path` resolves them into
  `chronicler`. Existing handlers in `roster/chronicler/api/router.py`
  use this pattern and continue to pass.
- A guardrail test under
  `tests/chronicler/test_aggregation_no_cross_schema.py` performs static
  analysis: it parses each handler module, extracts SQL string literals
  (including `text()` constructs, `psycopg.sql` composables, and asyncpg
  query strings), and validates every relation reference against a known
  list of `chronicler.*` relations. Schema-qualified references outside
  `chronicler.*` cause the test to fail. Bare references whose names do
  not appear in the `chronicler` relation list also fail; the test
  cannot infer `search_path` resolution at parse time, so handler
  authors MUST either schema-qualify cross-schema reads (which will
  fail the test) or rely solely on names known to live in
  `chronicler.*`.
- Backfill: the same test runs against the existing handler set today.
  If any existing handler references a non-`chronicler` relation, the
  finding becomes a discovered-from bead for remediation rather than
  blocking this change.

## Migration / Rollout

1. Land `add-dashboard-chronicles` openspec change; gate-validate.
2. Add `chronicler.tier2_cache` migration if used (only when D9/D10 are
   implemented; can be skipped in MVP cut if day-close cache is
   in-memory or deferred).
3. Implement aggregation module + endpoints + guardrail tests.
4. Implement source-state endpoint + Pydantic models.
5. Frontend: scaffold `/chronicles` page shell; add nav entry.
6. Implement Gantt component.
7. Implement aggregations panel (pie + stacked bar).
8. Add `maplibre-gl` dependency; implement map widget shell.
9. Wire playhead binding.
10. Sibling-unlock beads (OwnTracks adapter, Steam adapter, Google
    Health adapter, Meals projection, Home Assistant projection)
    proceed independently and unblock lanes incrementally.
11. Update `about/lay-and-land/components.md`; close RFC 0014 "Open
    Questions" item.

## Open Questions

- **Day-close cache table — durable or in-memory?** Durable
  (`chronicler.tier2_cache`) is preferred for correctness across daemon
  restarts; in-memory is cheaper. Defaulting to durable; revisit if
  storage size becomes a problem.
- **Cross-source aggregation tie-breaking when categories collide
  (same window, two `work` episodes from different butlers)**: keep
  both rows in the corrected view; aggregation sums their durations
  even if they overlap. UI shows them as separate stacked bars within
  the lane. No deduplication in v1.
- **Calendar `location` extraction for map binding**: the calendar
  butler stores `location` as a free-text field. Geocoding is OUT of
  scope. If the field is a recognizable lat/lng (or address that the
  client can handle), map widget MAY pan; otherwise no-op. No new
  geocoding service.

## References

- RFC 0014 (Chronicler Time Butler) — §D5 sparse interpretation,
  §D7 API surface, "Open Questions" L253–262.
- RFC 0007 (Dashboard and API Surface) — auto-discovery, response
  envelopes, sidebar nav structure.
- RFC 0006 (Database schema and isolation) — schema-qualified reads.
- RFC 0010 (Cross-butler briefing exception) — precedent for
  Chronicler's cross-schema read pattern (we do NOT extend it; we
  consume only from `chronicler.*`).
- `about/heart-and-soul/v1.md` — v1 scope and dashboard placement.
- `about/heart-and-soul/security.md` L172–175 — OwnTracks retention.
- `about/heart-and-soul/vision.md` L75–78 — deterministic batch
  aggregation pattern.
- `chronicler-api/spec.md` L31, L47, L120–122, L124–129, L131–142 —
  filters, tombstone exclusion, error shape, timeline-route fence.
- `dashboard-shell/spec.md` — sidebar nav structure, auto-refresh
  hook standard.
- `craft-and-care/performance-discipline.md` — measure-before-optimize.
- `craft-and-care/testing-and-verification.md` — guardrail test pattern.
