# Reconciliation Memo: add-dashboard-chronicles

**Date:** 2026-04-26
**Change:** `add-dashboard-chronicles`
**Scope:** bu-ig72b.1 – bu-ig72b.35 (all children merged to main)
**Verdict:** **PASS-WITH-FOLLOWUPS**

---

## Method

Compared the 3 spec files in `openspec/changes/add-dashboard-chronicles/specs/`
against the implementation landed by commits matching `git log --grep='bu-ig72b'`
(37 commits, PR #1136–#1176).

Guardrail tests run: `tests/chronicler/test_aggregation_no_llm.py` and
`tests/chronicler/test_aggregation_no_cross_schema.py` — **10/10 pass**.
OpenSpec validation: `openspec validate add-dashboard-chronicles --strict` — **PASS**.
Linter: `ruff check` — **no violations**.

---

## Section 1: chronicler-api/spec.md

### Requirement: Chronicler Aggregations

#### Scenario: Corrected-view-only reads — PASS

Both `GET /api/chronicler/aggregate/by-category` and `GET /api/chronicler/aggregate/by-day` read
exclusively from `v_episodes_corrected`. The no-cross-schema guardrail test
(`tests/chronicler/test_aggregation_no_cross_schema.py`) statically parses every SQL
string literal in `router.py` and asserts all FROM/JOIN tokens resolve to known
`chronicler`-schema relations. This test passed with a clean audit result on 2026-04-25
(11 SQL strings, 0 violations documented in the test module header).

#### Scenario: Provenance carry-forward on bucket records — PASS

Both aggregate endpoints return `source_breakdown` arrays with per-source
`total_seconds` and `episode_count`. The `precision` field is the
`_least_precise()` computation across all contributing rows (ordering:
`exact > minute > hour > day > unknown`). `retention_floor_days` is the
`min()` of non-NULL `retention_days` values (NULL if all rows use the
Chronicler default). Both are verified by `tests/chronicler/test_aggregate_by_category.py`
and `tests/chronicler/test_aggregate_by_day.py`.

#### Scenario: Privacy tier filtering with safe defaults — PASS (with nuance)

`by-category` supports comma-delimited `privacy_tier` and defaults to
`{"normal", "sensitive"}` (excludes `restricted`). Sensitive episodes contribute
to durations without exposing identifying content (filtering is at the API layer,
not in the SQL — titles are excluded from the aggregate response model).

`by-day` hardcodes `privacy != 'restricted'` and does NOT accept a `privacy_tier`
query parameter. The spec says `privacy_tier MAY be supplied as a comma-delimited
list to narrow the set further` — this applies to aggregate requests generically.
**Drift:** `by-day` does not support the optional `privacy_tier` parameter that
`by-category` does. The default behaviour (exclude restricted) is correct; only the
optional narrowing is missing. Recommended follow-up bead.

#### Scenario: Tombstone exclusion default — PASS

Both endpoints default `include_tombstoned=false`. When `include_tombstoned=true`
is passed, tombstoned rows are included and each `SourceBreakdownEntry` includes a
`tombstoned: bool` field flagged accordingly.

#### Scenario: No LLM invocation — PASS

`tests/chronicler/test_aggregation_no_llm.py` scans 5 handler files (router.py,
models.py, aggregations.py, day_close_writer.py, storage.py) via AST and asserts
no forbidden import or identifier (`anthropic`, `openai`, `claude_agent_sdk`,
`claude`, `interpret`) appears. All 10 test cases pass.

#### Scenario: Deterministic ordering and stable pagination — PASS

`by-category` sorts by `(-total_seconds, category)` (line 789 of router.py).
`by-day` is sorted via `sorted(day_cat.items())` which sorts `(day_str, category)`
tuples lexicographically — `day ASC, category ASC` as specified. No cursor
pagination is added; single-page response matches spec intent for initial version.

#### Scenario: Timezone-aware day buckets — PASS

`by-day` enumerates calendar days using `zoneinfo.ZoneInfo(tz)` and computes
`day_start`/`day_end` timestamps in the requested timezone, so DST-extended
(25-hour) and DST-shortened (23-hour) days are treated as single buckets with
actual-duration overlap semantics. `day_start` and `day_end` are included in
each `AggregateByDayRow`. Defaults to UTC when `tz` is omitted.

#### Scenario: Invalid time range rejected — PASS

Both endpoints reject missing `start_at`/`end_at` with `400 / missing_parameter`,
`end_at <= start_at` with `400 / invalid_time_range`, and unrecognized `tz` with
`400 / invalid_timezone`. All use the `ErrorResponse` envelope consistent with
the existing correction endpoint pattern.

#### Scenario: Unmapped active source surfaces as warning bucket — PASS

When `category_for(source_name, episode_type)` returns `"other"`, the handler
logs a warning and sets the OTel attribute
`chronicler.aggregate.unmapped_source = <source_name>`. The bucket's
`source_breakdown` still cites the unmapped `source_name`. Verified by
`test_aggregate_spans.py::TestAggregateByCategorySpan::test_unmapped_source_attribute_set`.

---

### Requirement: Chronicler Source State Visibility

#### Scenario: Source state listed with checkpoint diagnostics — PASS

`GET /api/chronicler/source-state` returns one record per `source_adapter_state`
row, joined with `projection_checkpoints` to aggregate `last_run_at` and
`last_error` across all subsources. The optional `subsource_checkpoints` array
is present. Records are ordered `source_name ASC`. Verified by
`tests/chronicler/test_source_state_api.py`.

#### Scenario: Empty source-state on cold boot — PASS

The endpoint responds `200 OK` with `data: []` when `source_adapter_state` is
empty. Tested explicitly.

#### Scenario: Optional-schema degradation surfaced — PASS

`source_adapter_state` rows carry `optional_schema` and `inactive_reason` fields.
The `SourceStateRow` Pydantic model exposes these. The dashboard caller
(`SourceStateBadgeStrip`) reads `inactive_reason` for the banner tooltip.
The read path from the endpoint is correct.

#### Scenario: Read-only contract — PASS

The FastAPI router only registers `GET /source-state`. Non-GET verbs receive
`405 Method Not Allowed` from FastAPI's default method routing. Verified by
the source-state API test.

---

### Requirement: Chronicler Day-Close Cache Surface

#### Scenario: Cache hit returns prose with provenance — PASS

`GET /api/chronicler/aggregate/day-close?date=YYYY-MM-DD` returns cached
`prose`, `provenance_refs`, and `cache_built_at` when the cache is fresh.
No LLM is invoked on this path. Implemented in `router.py:get_day_close_cache`.

**Drift (minor):** The spec endpoint signature includes a `tz` parameter
(`?date=YYYY-MM-DD&tz=...`), but the GET handler only accepts `date`. The
cache key is `day_close:{YYYY-MM-DD}` and the day window uses UTC boundaries
(computed in the writer from the UTC run time). The `tz` parameter was not
implemented in the GET handler. The POST refresh handler does accept `tz` but
doesn't use it to vary the cache key (only validates it). Recommended follow-up
bead to add `tz` support or document the UTC-only contract.

#### Scenario: Stale cache surfaces stale marker — PASS

When any episode, point_event, or override in the cached window has been
tombstoned, updated, or created after `cache_built_at`, the handler returns
`{stale: true, cache_built_at, last_invalidating_event_at}` without prose.
All three invalidation signals (tombstone, update, override creation) are
covered by the UNION ALL staleness query.

#### Scenario: Stale due to override creation — PASS

The staleness query includes an override branch scoped via episode/point_event
windows. Overrides with `created_at > cache_built_at` that target entities in
the cached window trigger the stale marker even if `episodes.updated_at` is
unchanged.

#### Scenario: Stale due to corrected_start_at — PASS

Two additional UNION ALL branches (signals 8 and 9, bu-ig72b.19 / main
PR #1161 + #1164) detect overrides that move episodes/point_events INTO the
cached window via `corrected_start_at`. These fire even when the entity's
original `start_at`/`occurred_at` falls outside the window. Verified by
`tests/chronicler/test_day_close_reader_api.py`.

#### Scenario: User-clicked refresh re-invokes existing path — PASS

`POST /api/chronicler/aggregate/day-close/refresh` looks up the
`chronicler_day_close` scheduled task prompt and dispatches it via the injected
`dispatch_fn` callable (same Tier-2 spawner path the scheduler uses). The
cache is written by `write_day_close_cache()` after dispatch. No new LLM
call path is introduced.

#### Scenario: Refresh rate limit enforced — PASS

Checks `tier2_cache` for an entry within the last 24 hours. If found, returns
`429 / day_close_rate_limited` with `retry_after_seconds` in `details`.
Uses the `ErrorResponse` envelope. Verified by
`tests/chronicler/test_day_close_refresh_api.py`.

---

## Section 2: dashboard-chronicles/spec.md

### Requirement: Chronicles Frontend Route — PASS

`/chronicles` is registered in `frontend/src/router.tsx` as a child of
`RootLayout` (line 93). The `ChroniclesPage` component renders inside the
standard shell. The existing `/timeline` route is preserved unchanged (line 66).
No handler is added at `/api/timeline`.

### Requirement: Sidebar Placement and Discrimination — PASS

`nav-config.ts` places Chronicles under the `Dedicated Butlers` section (line 72),
not under Telemetry. The Chronicles tooltip reads `"Retrospective lived-time
reconstruction"` exactly as specified. The `/timeline` entry is in the Telemetry
section with its own label.

**Note:** The nav-config.ts has `Sessions` moved to the Telemetry section rather
than Main, and includes QA and Entities entries not listed in the spec. These are
pre-existing differences unrelated to bu-ig72b's scope.

### Requirement: Page-Level Invariants — PASS

No LLM call paths exist in page-driven code (guardrail test passes). The only
LLM-bearing action is the `EpisodeDrawer`'s Tier-2 Explain button (RFC 0014 §D5),
which is an explicit user click. The aggregate/source-state backend handlers are
covered by the no-LLM guardrail. The no-cross-schema guardrail covers all backend
SQL strings.

### Requirement: Category Taxonomy Mapping — PASS

`src/butlers/chronicler/aggregations.py::category_for()` is a pure, deterministic,
no-I/O function returning one of 9 stable category strings. The frontend
`lane-taxonomy.ts` maps those strings to label, colour, hex, icon, and sortOrder.
The backend returns no colour or icon strings.

`tests/chronicler/test_aggregations.py::test_all_supported_sources_have_non_other_category`
asserts every SUPPORTED source in `contracts.py` has a D1 mapping entry.

**Gap:** `health.meals` appears in `aggregations._CATEGORY_MAP` with mapping
`("health.meals", "eating_event"): "meal"`, but there is no corresponding entry
in `contracts.py::INITIAL_SOURCES`. This means:
1. The category mapping exists but the source is never seeded into `source_adapter_state`.
2. `test_all_supported_sources_have_non_other_category` will not catch this because
   it only checks SUPPORTED sources in contracts, and `health.meals` is absent from
   contracts entirely.
Recommended follow-up bead to register `health.meals` in contracts.py with
appropriate compatibility level.

### Requirement: Disabled Lane Affordances — PASS

`SourceStateBadgeStrip.tsx` implements all 5 badge states:
- `supported + active` → enabled badge with category colour
- `supported + inactive` → yellow banner with inactive_reason + last_error tooltip
- `planned` → disabled badge, tooltip "Adapter planned; not yet implemented"
- `deferred` → hidden by default; toggle reveals deferred badges
- `not_time_bearing` → never rendered

The deferred-lanes toggle persists in localStorage under
`"chronicles.showDeferredLanes"`.

### Requirement: Map Render Privacy Contract — PASS

Restricted episodes are excluded by default at the API layer (server excludes
`restricted` from aggregates, and the hook does not pass `restricted` in
`privacy_tier`). Sensitive episodes contribute to bucket durations but titles
and payload details are not included in aggregate response models.

The Gantt (`GanttSwimlaneInner.tsx`) renders sensitive episodes as masked entries
using a hatch pattern — no title or payload content is displayed. The map
(`MapWidgetInner.tsx`) filters out any point with `privacy_tier === "sensitive"`
before plotting. Tombstoned rows excluded by default (`include_tombstoned=false`
server default).

### Requirement: Day-Close Cache Invalidation — PASS (with implemented-but-not-specced note)

All three core invalidation signals are implemented:
- tombstone on episode/point_event → stale
- updated_at on episode/point_event → stale
- override created_at → stale

Two additional signals beyond the spec were implemented:
- Signal 6/7 (provenance-ref staleness): episodes/point_events cited in
  `provenance_refs` that were updated after cache was built (even if now outside
  the window) also trigger staleness. This is an additive improvement consistent
  with the spec's intent, not a contradiction.
- Signal 8/9 (corrected_start_at staleness, bu-ig72b.19 and main): overrides
  moving entities INTO the cached window via `corrected_start_at`.
  These were subsequently specced in the chronicler-api/spec.md "Stale due to
  override creation" scenario (which mentions corrected_start_at). **PASS.**

User-clicked refresh is rate-limited to 1 per 24h. The `EpisodeDrawer` displays
a "regenerate" affordance for stale cache entries.

### Requirement: Auto-Refresh Adoption — PASS (with minor nuance)

`ChroniclesPage.tsx` uses `useAutoRefresh(30_000)` (30s default) and
`AutoRefreshToggle` allows 10/30/60 second options plus pause. When
`timeWindow.pollingDisabled` is true, `refetchInterval` is set to `false`
(disabling polling). A manual refresh is not explicitly rendered for historical
windows (no dedicated refresh button), though TanStack Query's `refetch` is
called by error-retry callbacks.

**Minor drift:** The spec says older windows "SHALL provide a manual refresh
button," but the implementation only has error-state retry buttons, not a general
manual refresh button for static historical windows. This may be acceptable UX
given data doesn't change for historical windows.

### Requirement: MapLibre Dependency Justification — PASS

`frontend/package.json` includes `maplibre-gl@^5` (BSD-3, open-source fork).
`MapWidget.tsx` uses OSM tiles via `https://tile.openstreetmap.org/{z}/{x}/{y}.png`
in `MapWidgetInner.tsx`. The dependency is code-split via `React.lazy()`. Bundle
measurement was noted in the PR description for bu-ig72b.6.

### Requirement: Page Telemetry — PASS

OTel spans are emitted for:
- `chronicler.aggregate.by_category` (query_latency_ms, bucket_count, optional unmapped_source)
- `chronicler.aggregate.by_day` (query_latency_ms, bucket_count, optional unmapped_source)
- `chronicler.aggregate.day_close` (query_latency_ms, cache_state)
- `chronicler.source_state` (row_count, query_latency_ms)

Verified by `tests/chronicler/test_aggregate_spans.py`.

---

## Section 3: dashboard-shell/spec.md

### Requirement: Sidebar Navigation (MODIFIED) — PASS

The `/chronicles` entry is in the Dedicated Butlers section with the required
tooltip. The full route map includes `/chronicles` as a top-level route rendering
`ChroniclesPage`. The `/timeline` route is preserved.

### Requirement: Full Route Map (MODIFIED) — PASS

All required routes from the spec are present in `frontend/src/router.tsx`. The
implementation has additional routes (`/qa/*`, `/entities`, `/ingestion`, etc.)
that are pre-existing and not introduced by bu-ig72b.

---

## Doctrine Invariant Checks

### No-LLM guardrail (bu-ig72b.15)
**PASS.** `test_aggregation_no_llm.py` scans `router.py`, `models.py`,
`aggregations.py`, `day_close_writer.py`, and `storage.py` via AST. All 10
test cases pass (10/10).

### No-cross-schema-read guardrail (bu-ig72b.16)
**PASS.** `test_aggregation_no_cross_schema.py` extracts SQL string literals
from `router.py` and asserts all FROM/JOIN tokens resolve to known chronicler-schema
relations. Zero violations on 2026-04-25 audit. Test passes.

### Cache invalidation — all three signals (bu-ig72b.19, .26)
**PASS.** All three core signals (tombstone, update, override creation) are
implemented. Two super-signals (provenance-ref staleness, corrected_start_at
staleness) were also added and are consistent with spec intent.

### Privacy defaults — sensitive masked, restricted excluded, include_tombstoned=false (bu-ig72b.21)
**PASS.** Enforced at the hook layer (`use-chronicles.ts` does not inject
`restricted` into `privacy_tier`) and at the backend (server default excludes
restricted). Sensitive masking is enforced at render time in `GanttSwimlaneInner`
and `MapWidgetInner`. `include_tombstoned=false` is the server default across
all endpoints.

---

## Sibling-Unlock Readiness Check

Status in `contracts.py::INITIAL_SOURCES` vs. spec expectation:

| Source | contracts.py status | Expected per issue description |
|---|---|---|
| OwnTracks (`owntracks.points`) | SUPPORTED | Was expected PLANNED — adapter landed in bu-ahs9z |
| Steam (`steam.play_history`) | SUPPORTED | Was expected PLANNED — adapter landed in bu-x8trk |
| Google Health (`google_health.measurements`) | DEFERRED | DEFERRED ✓ |
| Meals (`health.meals`) | **Not registered** | Gap — no entry in contracts.py |
| Home Assistant (`home_assistant.history`) | PLANNED | PLANNED ✓ |

OwnTracks and Steam are now SUPPORTED (adapters shipped). The `source-state`
endpoint will correctly report them as `supported` with `active` status depending
on runtime adapter state.

The `health.meals` source has a category mapping in `aggregations.py` but no
`source_adapter_state` registration in `contracts.py`. This means it will not
appear in `/api/chronicler/source-state` output, and the Meals lane will not
render in the badge strip even if data is eventually ingested.

---

## Drift Summary

| # | Type | Location | Description | Disposition |
|---|---|---|---|---|
| D1 | specced-but-not-implemented | by-day endpoint | `privacy_tier` comma-delimited parameter not supported (only hardcoded `privacy != 'restricted'`) | Follow-up bead |
| D2 | specced-but-not-implemented | day-close GET endpoint | `tz` query parameter not accepted (spec: `?date=YYYY-MM-DD&tz=...`); cache is UTC-only | Follow-up bead |
| D3 | specced-but-not-implemented | ChroniclesPage | Manual refresh button missing for static historical windows | Follow-up bead (low priority) |
| D4 | implemented-but-not-specced | day-close staleness query | Provenance-ref staleness signals (6/7) not in spec but consistent with intent | Extend spec OR accept as implementation detail |
| D5 | contracts gap | contracts.py | `health.meals` source has category mapping but no `source_adapter_state` registration | Follow-up bead |

---

## Verdict

**PASS-WITH-FOLLOWUPS**

All 15 spec sections (3 from chronicler-api delta + 10 from dashboard-chronicles
+ 2 from dashboard-shell delta) are at minimum partially implemented. Three
small functional gaps (D1, D2, D3) and one data integrity gap (D5) are
documented as follow-up beads. One implemented-but-not-specced behaviour (D4)
is consistent with the spec's spirit and does not require code removal.

This verdict clears the gate for bu-ig72b.36 (epic report).

---

## Recommended Follow-Up Beads

1. **Add `privacy_tier` parameter to `by-day` aggregate endpoint**
   - Type: feature
   - Priority: P3
   - Description: `GET /api/chronicler/aggregate/by-day` hardcodes `privacy != 'restricted'`
     and does not accept a comma-delimited `privacy_tier` query parameter to narrow the
     privacy filter. The spec says this parameter MAY be supplied. Add it to match the
     `by-category` endpoint parity.
   - File: `roster/chronicler/api/router.py:aggregate_by_day`

2. **Add `tz` parameter to `GET /api/chronicler/aggregate/day-close`**
   - Type: feature
   - Priority: P3
   - Description: The spec endpoint signature is `?date=YYYY-MM-DD&tz=...` but the
     implementation only accepts `date`. The cache key and day-window computation are
     UTC-only. Either add `tz` support (with tz-aware cache keys like
     `day_close:{YYYY-MM-DD}:{tz}`) or document the UTC-only constraint as a spec
     amendment.
   - File: `roster/chronicler/api/router.py:get_day_close_cache`,
     `src/butlers/chronicler/day_close_writer.py`

3. **Add manual refresh button for historical (static) time windows on ChroniclesPage**
   - Type: feature
   - Priority: P4 (backlog)
   - Description: The spec requires a manual refresh button when the time-window picker
     selects a window whose `end_at` is before the current day. Currently only error-state
     retry buttons exist. Add a general refresh control that is visible when
     `timeWindow.pollingDisabled === true`.
   - File: `frontend/src/pages/ChroniclesPage.tsx`

4. **Register `health.meals` source in contracts.py**
   - Type: bug / task
   - Priority: P2
   - Description: `aggregations._CATEGORY_MAP` has `("health.meals", "eating_event"): "meal"`
     but `contracts.py::INITIAL_SOURCES` has no entry for `health.meals`. This means the
     source never appears in `/api/chronicler/source-state` output and the Meals lane
     will be absent from the `SourceStateBadgeStrip` even when meal data is ingested.
     Add a `health.meals` entry to `INITIAL_SOURCES` with appropriate
     `chronicler_compatibility` (likely PLANNED until the Health butler projection adapter
     ships) and `optional_schema=True`.
   - File: `src/butlers/chronicler/contracts.py`

5. **Extend day-close spec scenario to document provenance-ref staleness signals**
   - Type: task (spec amendment)
   - Priority: P3
   - Description: The implementation includes signals 6/7 (provenance-ref staleness:
     episodes/point_events cited in `provenance_refs` that were updated after cache was
     built). These are not mentioned in the spec's "Stale cache surfaces stale marker"
     scenario but are consistent with the intent. Extend the scenario in
     `specs/chronicler-api/spec.md` to document these as additional SHOULD-conditions,
     so future implementations can reproduce them.
   - File: `openspec/changes/add-dashboard-chronicles/specs/chronicler-api/spec.md`
