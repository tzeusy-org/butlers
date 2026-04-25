# Chronicles — Dashboard Page over Chronicler

## Why

RFC 0014 (Chronicler) §"Open Questions" L253–262 explicitly anticipates the
dashboard as Chronicler's primary interaction surface, but no dashboard
surface exists today. The user has only two paths to retrospective time:
direct API calls (`curl /api/chronicler/episodes`) or conversational queries
that route to Chronicler via Switchboard. Neither answers the actual
operational question the user has every day: *what was I up to, at any
given time?* — across overlapping concurrent activities (work + music +
travel + sleep + meal + game), with location replay and aggregate
breakdowns, and without paying an LLM call per scroll.

The existing `/timeline` route in the dashboard is preserved as the
operational cross-butler ops stream (sessions / notifications / errors). It
does NOT solve the lived-time problem. `chronicler-api/spec.md` L131–142
explicitly fences this off ("any future user-facing timeline route SHALL
require a separate dashboard/API spec amendment"). This change IS that
amendment.

A retrospective view is structurally different from `/timeline`: it
overlays multiple lanes that overlap in time (Chronicler episodes), it
needs aggregations (time-spent-by-category) that are expensive to derive
on every load, and it needs spatial replay tied to the same time scrubber.
None of these are served by the existing API surface.

## What Changes

- **New frontend route `/chronicles`** — three vertically stacked widgets
  over a shared time-window picker:
  1. **Gantt swimlane chart** — labelled, category-coloured activity lanes
     with overlap support, scrubbing across hours and days. Lanes derived
     from `(source_name, episode_type) → category` deterministic mapping.
  2. **Map widget with playhead** — replays OwnTracks (and future
     location-bearing) point events along the same timeline; playhead is
     bound to the Gantt scrubber.
  3. **Aggregations panel** — pie chart for time-spent-by-category, plus a
     per-day stacked bar and longest-streak callouts.

- **New `chronicler-api` requirements (delta to existing capability):**
  - `Requirement: Chronicler Aggregations` — read-only endpoints under
    `/api/chronicler/aggregate/*` (initial set: `/by-category`,
    `/by-day`). Corrected-view-only reads, full provenance carry-forward,
    `privacy_tier` and `include_tombstoned` query params honored, no LLM,
    no cross-schema reads, deterministic ordering, timezone-aware day
    buckets, structured 400 on invalid time range.
  - `Requirement: Chronicler Source State Visibility` — `GET
    /api/chronicler/source-state` exposes `source_adapter_state` rows so
    the frontend can render disabled-lane affordances for PLANNED /
    DEFERRED sources. Read-only; no mutation.

- **New capability spec `dashboard-chronicles`** — page surface contract:
  route `/chronicles`, sidebar placement under "Dedicated Butlers"
  (NOT Telemetry), category taxonomy, lane disabled-state rules, map
  privacy/retention render contract, day-close cache invalidation rule,
  auto-refresh adoption, no-LLM and no-cross-schema page invariants.

- **Frontend dependency addition**: `maplibre-gl` (BSD-3) for the map
  widget. OpenStreetMap tiles, no token. Alternatives `react-leaflet`
  (limited GPU scaling) and `mapbox-gl` (token requirement violates
  self-hosting alignment) considered and rejected.

- **`useAutoRefresh` adoption**: Chronicles defaults to 30 s on the
  "today" window, static (no polling) on older windows. Reuses the
  existing hook; coins no new defaults.

- **Cache invalidation rule for cached `chronicler_day_close` Tier-2 prose**:
  cache entries store `(start_window, end_window, cache_built_at)`; render
  is suppressed and replaced with a "stale" affordance whenever an episode
  in window has `tombstone_at > cache_built_at`, `updated_at >
  cache_built_at`, or any override row in window has `created_at >
  cache_built_at`. User-clicked refresh re-invokes the existing scheduled
  `chronicler_day_close` Tier-2 entry point per RFC 0014 §D5
  (rate-limited 1/day/window). NO new LLM path.

- **Documentation updates**: `about/lay-and-land/components.md` §4a
  Chronicler row updated with new aggregate + source-state routes; RFC
  0014 "Open Questions" closed (the dashboard surface question becomes
  resolved).

## Capabilities

### New Capabilities

- `dashboard-chronicles` — page surface contract for `/chronicles`,
  including widget composition, taxonomy mapping, privacy/retention
  render rules, cache invalidation contract, and the page-level no-LLM /
  no-cross-schema invariants.

### Modified Capabilities

- `chronicler-api` — adds three new Requirements (Aggregations, Source
  State Visibility, Day-Close Cache Surface). The closed endpoint set in
  RFC 0014 §D7 is explicitly extended; provenance, privacy, tombstone,
  and no-LLM rules carry forward unchanged.
- `dashboard-shell` — modifies the Sidebar Navigation and Full Route Map
  Requirements to register the `/chronicles` route under Dedicated
  Butlers and document the Chronicles vs Timeline tooltip discrimination.

## Impact

- New frontend page, components, and hooks under `frontend/src/pages/`,
  `frontend/src/components/chronicles/`, `frontend/src/hooks/`,
  `frontend/src/api/`.
- New backend routes in `roster/chronicler/api/router.py` plus models in
  `roster/chronicler/api/models.py`.
- New Pydantic schemas for aggregate bucket records and source-state rows.
- New SQL views or query helpers in `src/butlers/chronicler/storage.py`
  (or a new `aggregations.py` module) for category/day rollups over
  corrected views.
- Sidebar nav config update in
  `frontend/src/components/layout/nav-config.ts`.
- New `maplibre-gl` dependency in `frontend/package.json` (BSD-3).
- New OTel spans `chronicler.aggregate.*` and `chronicler.source_state`;
  new Grafana panels for aggregation latency.
- Documentation: `about/lay-and-land/components.md` §4a Chronicler row;
  RFC 0014 "Open Questions" closure note.
- No schema migrations to `chronicler.episodes` /
  `chronicler.point_events` — taxonomy is derived, not stored. One
  additive migration introduces `chronicler.tier2_cache` for the
  day-close prose cache (see design.md §D8).
- No changes to the existing `/api/timeline` route or its consumers.

## Deferred

- **OwnTracks projection adapter**: Map widget will be an empty shell
  until OwnTracks projection lands. Tracked as a sibling bead (parent of
  this epic), not in scope here.
- **Steam projection adapter**: Gaming lane disabled until Steam adapter
  lands. Sibling bead.
- **Google Health projection adapter** (sleep, activity): Sleep lane
  disabled until Google Health adapter lands; the connector itself is
  also DEFERRED in `chronicler-source-compatibility`. Sibling bead.
- **Meals projection** (Health butler): Eating lane disabled. The `health.meals`
  table has `eaten_at` only (no end), so projection is point-event
  treatment with optional user-supplied duration; deferred to a sibling
  bead.
- **Home Assistant presence projection**: Home lane disabled. Sibling bead.
- **Multi-day "compare two windows" view, custom dashboards, exports
  (CSV/PDF)**: future iteration; not in scope.
- **Annotations / tags directly on Chronicles episodes**: corrections
  endpoint already exists; no new annotation surface in this change.
- **Streaming map playback at sub-second resolution**: playhead snaps to
  the nearest point event; smoothing is out of scope.
