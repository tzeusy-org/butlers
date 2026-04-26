# Epic Report: Chronicles Dashboard Page (bu-ig72b)

**Epic:** bu-ig72b — Chronicles dashboard page (consume Chronicler retrospective time)
**Date:** 2026-04-26
**Reconciliation verdict:** PASS-WITH-FOLLOWUPS (see `docs/reports/2026-04-26-add-dashboard-chronicles-reconciliation.md`)
**OpenSpec change:** `add-dashboard-chronicles`
**Status:** All 36 implementation children closed. Reconciliation (bu-ig72b.37, PR #1182) merged.

---

## 1. Outcome Summary

### What shipped

The Chronicles dashboard page delivers a retrospective time-intelligence surface
on the butlers dashboard at `/chronicles`. It was designed to close RFC 0014's
open question about how Chronicler-owned retrospective data surfaces to the
operator — the answer is a dedicated page with three composable widgets:
Gantt swimlanes, a map with OwnTracks trail, and an aggregations panel with
charts and streak callouts.

Every component of the original epic scope shipped:

- **Backend aggregate API** (`/api/chronicler/aggregate/by-category`,
  `/api/chronicler/aggregate/by-day`, `/api/chronicler/aggregate/day-close`,
  `POST /api/chronicler/aggregate/day-close/refresh`) — all handlers read
  exclusively from `chronicler.v_episodes_corrected`.
- **Source-state API** (`/api/chronicler/source-state`) — lists adapter health
  and readiness per source, powering the badge strip.
- **Frontend page** (`ChroniclesPage.tsx`) — registered at `/chronicles`,
  inside the standard dashboard shell, under Dedicated Butlers in the sidebar.
- **Gantt swimlanes** — overlap-aware SVG Gantt with privacy masking and
  Radix tooltip (bu-ig72b.28, .29, .30).
- **Map widget** — code-split `maplibre-gl` with OwnTracks trail layer
  (bu-ig72b.14, .35); playhead binding drives both Gantt cursor and map marker
  (bu-ig72b.23).
- **Aggregation charts** — stacked bar by-day × category (bu-ig72b.33),
  pie chart by-category (bu-ig72b.32), streak callouts (bu-ig72b.34).
- **Episode drilldown drawer** with Tier-2 Explain button (bu-ig72b.31) —
  the single explicit LLM path, user-initiated only.
- **Guardrail tests** — no-LLM AST scan (bu-ig72b.15) and no-cross-schema
  SQL parser (bu-ig72b.16) both pass in CI.
- **Latency benchmarks** — by-category P95 16.8 ms, by-day P95 45.7 ms,
  against a target of 200 ms on synthetic 1050-episode fixture.
- **OTel spans** — all four new endpoints instrumented (bu-ig72b.17).
- **Docs housekeeping** — RFC 0014 Open Questions closed (bu-ig72b.8),
  `about/lay-and-land/components.md` §4a updated (bu-ig72b.7).

The OwnTracks and Steam sibling adapters (bu-ahs9z, bu-x8trk) shipped before
the epic closed and are already SUPPORTED in `contracts.py`.

### What was deferred

Three small functional gaps were found during reconciliation (bu-ig72b.37) and
recorded as follow-up beads rather than blocking the epic:

- **D1:** `GET /api/chronicler/aggregate/by-day` does not accept the optional
  `privacy_tier` comma-delimited parameter that `by-category` supports (low risk;
  default is correct).
- **D2:** `GET /api/chronicler/aggregate/day-close` does not accept a `tz`
  parameter; the cache key and day-window are UTC-only. The spec anticipated
  `?date=YYYY-MM-DD&tz=...`.
- **D3:** ChroniclesPage lacks a general manual refresh button for static
  historical windows (spec: SHALL provide one; only error-state retry exists).

One data-integrity gap was also found:

- **D5:** `health.meals` has a category mapping in `aggregations.py` but no
  entry in `contracts.py::INITIAL_SOURCES`, so the Meals lane will never appear
  in the badge strip.

One tooling gap was discovered during report generation:

- **D6:** `openspec archive add-dashboard-chronicles` aborts because the
  `dashboard-shell` delta's Sidebar Navigation requirement uses normative
  "SHALL provide" language while the main spec uses indicative "provides" — the
  archiver's section-match fails on this text divergence. Filed as bu-zfdzl;
  fix is in progress via PR #1190 (open as of report date).

Reconciliation verdict: **PASS-WITH-FOLLOWUPS**. The verdict explicitly clears
this report bead (bu-ig72b.36) and marks `openspec archive add-dashboard-chronicles`
as safe to run once D6 (bu-zfdzl / PR #1190) merges.

---

## 2. Architecture

The Chronicles dashboard follows the standard butlers data flow — no new
infrastructure, no new LLM paths outside the user-triggered drilldown.

```
┌──────────────────────────────────────────────────────────┐
│  OpenSpec change: add-dashboard-chronicles               │
│  (chronicler-api delta + dashboard-chronicles +          │
│   dashboard-shell route-map delta)                       │
└──────────────────────────────────────────────────────────┘

Backend (Chronicler butler — roster/chronicler/)
─────────────────────────────────────────────────────────────

 chronicler.v_episodes_corrected  ←─────────────────────────┐
 chronicler.v_point_events_corrected                        │ corrected
                                                            │ views only
                ↓ (aggregate SQL, no cross-schema)          │
 ┌──────────────────────────────┐                           │
 │ GET /aggregate/by-category   │──→ AggregateByCategory[]  │
 │ GET /aggregate/by-day        │──→ AggregateByDay[]        │
 │ GET /aggregate/day-close     │──→ DayCloseCache (prose)   │
 │ POST /aggregate/day-close/refresh │──→ 202 Accepted       │
 │ GET /source-state            │──→ SourceStateRow[]        │
 └──────────────────────────────┘
          ↑ OTel spans (chronicler.aggregate.*, chronicler.source_state)

 chronicler.tier2_cache (day-close prose, staleness signals 1–9)
 ┌──────────────────────────────────────────────────────────┐
 │ Staleness signals:                                       │
 │  1. episode tombstoned                                   │
 │  2. episode updated_at changed                           │
 │  3. point_event tombstoned                               │
 │  4. point_event updated_at changed                       │
 │  5. override created_at (covers entry into window)       │
 │  6. provenance-ref episode updated (beyond window edge)  │
 │  7. provenance-ref point_event updated                   │
 │  8. override moving episode into window via             │
 │     corrected_start_at                                   │
 │  9. override moving point_event into window              │
 └──────────────────────────────────────────────────────────┘

Frontend (frontend/src/)
─────────────────────────────────────────────────────────────

 router.tsx → /chronicles → ChroniclesPage.tsx
              │
              ├── TimeWindowPicker (useTimeWindow, URL sync)
              ├── AutoRefreshToggle (useAutoRefresh, 30s for today)
              ├── SourceStateBadgeStrip ─── GET /source-state
              │
              ├── GanttSwimlane (SVG, overlap stacking)
              │     └── Radix tooltip → EpisodeDrawer (on click)
              │           └── [Tier-2 Explain button] ── day-close path
              │
              ├── MapWidget (React.lazy code-split, ~285 kB gzip async chunk)
              │     └── MapWidgetInner (maplibre-gl, OSM tiles)
              │           ├── point markers (normal/sensitive-filtered)
              │           └── LineString layer (OwnTracks trail)
              │
              └── Aggregations area
                    ├── AggregatePieChart (recharts, by-category)
                    ├── AggregateStackedBar (recharts, by-day × category)
                    └── StreakCallouts (longest contiguous run per category)

TanStack Query hooks (use-chronicles.ts):
  useChroniclesByCategory, useChroniclesByDay,
  useDayCloseCache, useSourceState

Guardrail tests (CI):
  test_aggregation_no_llm.py   — AST scan, 5 handler files, 0 violations
  test_aggregation_no_cross_schema.py — SQL parser, 11 strings, 0 violations
```

---

## 3. Spec Compliance Matrix

Sources: `openspec/changes/add-dashboard-chronicles/specs/{chronicler-api,dashboard-chronicles,dashboard-shell}/spec.md`

### chronicler-api delta (3 Requirements, 15 Scenarios)

| Requirement | Scenario | Status | Closing bead/PR | Notes |
|---|---|---|---|---|
| Chronicler Aggregations | Corrected-view-only reads | Implemented | bu-ig72b.9 / #1145, bu-ig72b.10 / #1141, bu-ig72b.16 | Guardrail test + 11 SQL strings audited |
| Chronicler Aggregations | Provenance carry-forward on bucket records | Implemented | bu-ig72b.9 / #1145, bu-ig72b.10 / #1141 | precision + retention_floor_days on all buckets |
| Chronicler Aggregations | Privacy tier filtering with safe defaults | Partially implemented | bu-ig72b.9 / #1145 | by-category supports `privacy_tier` param; by-day hardcodes `!= restricted` (D1 drift) |
| Chronicler Aggregations | Tombstone exclusion default | Implemented | bu-ig72b.9 / #1145, bu-ig72b.10 / #1141 | `include_tombstoned=false` default across both |
| Chronicler Aggregations | No LLM invocation | Implemented | bu-ig72b.15 / #1159 | AST guardrail test; 10/10 pass |
| Chronicler Aggregations | Deterministic ordering and stable pagination | Implemented | bu-ig72b.9 / #1145, bu-ig72b.10 / #1141 | by-cat: `-total_seconds, category`; by-day: `day ASC, category ASC` |
| Chronicler Aggregations | Timezone-aware day buckets | Implemented | bu-ig72b.10 / #1141 | zoneinfo.ZoneInfo; DST spring-forward/fall-back tested |
| Chronicler Aggregations | Invalid time range rejected | Implemented | bu-ig72b.11 / #1147 | 400/429 ErrorResponse envelopes on all aggregate endpoints |
| Chronicler Aggregations | Unmapped active source surfaces as warning bucket | Implemented | bu-ig72b.1 | `"other"` bucket + OTel `unmapped_source` attribute |
| Chronicler Source State Visibility | Source state listed with checkpoint diagnostics | Implemented | bu-ig72b.2 / #1138 | subsource_checkpoints array, last_run_at, last_error |
| Chronicler Source State Visibility | Empty source-state on cold boot | Implemented | bu-ig72b.2 / #1138 | `200 OK`, `data: []` |
| Chronicler Source State Visibility | Optional-schema degradation surfaced | Implemented | bu-ig72b.2 / #1138 | `optional_schema`, `inactive_reason` in SourceStateRow |
| Chronicler Source State Visibility | Read-only contract | Implemented | bu-ig72b.2 / #1138 | Only GET registered; 405 on non-GET |
| Chronicler Day-Close Cache Surface | Cache hit returns prose with provenance | Implemented | bu-ig72b.19 / #1144 | prose + provenance_refs + cache_built_at |
| Chronicler Day-Close Cache Surface | Stale cache surfaces stale marker | Implemented | bu-ig72b.19 / #1144, bu-ig72b.26 / #1161, #1164 | 9 staleness signals; all three core + corrected_start_at |
| Chronicler Day-Close Cache Surface | Stale due to override creation | Implemented | bu-ig72b.19 / #1144 | Override branch in UNION ALL staleness query |
| Chronicler Day-Close Cache Surface | Stale due to corrected_start_at | Implemented | signals 8+9 / PR #1161, #1164 | Both episode and point_event coverage |
| Chronicler Day-Close Cache Surface | User-clicked refresh re-invokes existing path | Implemented | bu-ig72b.26 / #1152 | POST refresh dispatches via existing Tier-2 spawner |
| Chronicler Day-Close Cache Surface | Refresh rate limit enforced | Implemented | bu-ig72b.26 / #1152 | 429 / day_close_rate_limited + retry_after_seconds |
| Chronicler Day-Close Cache Surface | `tz` parameter on GET day-close | Not implemented | — | D2 drift: cache is UTC-only; follow-up filed |

### dashboard-chronicles capability (10 Requirements)

| Requirement | Status | Closing bead/PR | Notes |
|---|---|---|---|
| Chronicles Frontend Route | Implemented | bu-ig72b.4, bu-ig72b.13 / #1142 | /chronicles in router.tsx; RootLayout shell |
| Sidebar Placement and Discrimination | Implemented | bu-ig72b.13 | Under Dedicated Butlers; tooltip = "Retrospective lived-time reconstruction" |
| Page-Level Invariants | Implemented | bu-ig72b.15 / #1159, bu-ig72b.16 | No-LLM guardrail + no-cross-schema guardrail both pass |
| Category Taxonomy Mapping | Implemented | bu-ig72b.1, bu-ig72b.5 | category_for() backend + lane-taxonomy.ts frontend; gap: health.meals not in contracts.py (D5) |
| Disabled Lane Affordances | Implemented | bu-ig72b.22 | SourceStateBadgeStrip: 5 badge states including deferred-toggle (localStorage) |
| Map Render Privacy Contract | Implemented | bu-ig72b.14 / #1143, bu-ig72b.29 / #1160 | Sensitive filtered from map; restricted excluded at API; Gantt hatch pattern |
| Day-Close Cache Invalidation | Implemented | bu-ig72b.19 / #1144, bu-ig72b.12 / #1140 | All 3 core signals + 6 super-signals; staleness shown in EpisodeDrawer |
| Auto-Refresh Adoption | Partially implemented | bu-ig72b.27 / #1149 | 30s polling for today; historical windows: polling disabled but no explicit manual refresh button (D3) |
| MapLibre Dependency Justification | Implemented | bu-ig72b.6 / #1136, bu-ig72b.14 / #1143 | BSD-3 license; OSM tiles; code-split async chunk (~285 kB gzip) |
| Page Telemetry | Implemented | bu-ig72b.17 / #1173 | OTel spans on all 4 endpoints with latency, bucket counts, cache state |

### dashboard-shell delta (2 Requirements)

| Requirement | Status | Closing bead/PR | Notes |
|---|---|---|---|
| Sidebar Navigation (MODIFIED) | Implemented | bu-ig72b.13 | /chronicles in Dedicated Butlers section |
| Full Route Map (MODIFIED) | Implemented | bu-ig72b.4, bu-ig72b.7 / #1171 | router.tsx + components.md §4a updated |

---

## 4. Performance Results

### Aggregate endpoint latency (bu-ig72b.18, PR #1154)

Measured on commodity dev hardware with synthetic fixture: 7 days × 50 episodes/day × 3 sources = 1050 episodes, 20 warmup + 200 measured iterations each.

| Endpoint | P50 | P95 | P99 | Target | CI threshold |
|---|---|---|---|---|---|
| GET /api/chronicler/aggregate/by-category | 12.6 ms | **16.8 ms** | 19.1 ms | 200 ms | 250 ms |
| GET /api/chronicler/aggregate/by-day | 32.5 ms | **45.7 ms** | 61.0 ms | 200 ms | 250 ms |

Both endpoints are comfortably below the P2 target (>10× margin on by-category, >4× on by-day). No index review bead required.

### MapLibre bundle size (bu-ig72b.6 / #1136, bu-ig72b.14 / #1143)

maplibre-gl (`^5.24.0`) is code-split via `React.lazy()` in `MapWidget.tsx`:

| Chunk | Size (gzip) |
|---|---|
| Main `index-*.js` delta from maplibre-gl addition | +0.26 kB (unchanged for practical purposes) |
| `MapWidgetInner` async chunk (new) | **285.50 kB** gzip |
| `MapWidgetInner` CSS (new) | 10.05 kB |

maplibre-gl is fully isolated in its own async chunk fetched only when `/chronicles` is visited. Main bundle remains under the 500 kB gzip threshold.

---

## 5. Sibling Unlock Status

The Chronicles page is designed to light up progressively as sibling source adapters ship. Status as of 2026-04-26:

| Sibling | Bead | Status | Notes |
|---|---|---|---|
| OwnTracks (GPS trail) | bu-ahs9z | **Closed / SUPPORTED** | Adapter shipped; trail rendering active (bu-ig72b.35 / #1176) |
| Steam (play history) | bu-x8trk | **Closed / SUPPORTED** | Adapter shipped (PR #1157); gaming lane active |
| Google Health (sleep/measurements) | bu-ru1rp | **Open** | DEFERRED in contracts.py; sleep lane will show as disabled |
| Meals (health butler → Chronicler) | bu-a512n | **Open** | Gap: `health.meals` missing from contracts.py entirely (D5) |
| Home Assistant (presence) | bu-bf6ll | **Open** | PLANNED in contracts.py; home lane shows as disabled |

OwnTracks and Steam shipped within this epic's timeline. The remaining three (Google Health, Meals, Home Assistant) are independent epics — Chronicles ships independently, and the badge strip correctly shows disabled/deferred states for unimplemented sources.

---

## 6. Follow-Up Beads

The following follow-ups were identified during the reconciliation pass (bu-ig72b.37). All are recommended as new beads, not blocking the epic close.

| # | Title | Priority | Key file |
|---|---|---|---|
| D1 | Add `privacy_tier` parameter to `GET /api/chronicler/aggregate/by-day` | P3 | `roster/chronicler/api/router.py:aggregate_by_day` |
| D2 | Add `tz` parameter to `GET /api/chronicler/aggregate/day-close` | P3 | `roster/chronicler/api/router.py:get_day_close_cache`, `src/butlers/chronicler/day_close_writer.py` |
| D3 | Add manual refresh button for historical time windows on ChroniclesPage | P4 | `frontend/src/pages/ChroniclesPage.tsx` |
| D4 | Extend day-close spec scenario to document provenance-ref staleness signals 6/7 | P3 | `openspec/changes/add-dashboard-chronicles/specs/chronicler-api/spec.md` |
| D5 | Register `health.meals` source in contracts.py (currently mapping exists, registration absent) | P2 | `src/butlers/chronicler/contracts.py` |
| D6 | Fix openspec archive: dashboard-shell delta SHALL/provides language mismatch | P2 | `openspec/changes/add-dashboard-chronicles/specs/dashboard-shell/spec.md` — bead bu-zfdzl, fix in PR #1190 (open) |

See the reconciliation memo for full descriptions and file locations:
`docs/reports/2026-04-26-add-dashboard-chronicles-reconciliation.md`

---

## 7. Risks and Reviewer Notes

### Known risks

| Risk | Severity | Mitigation |
|---|---|---|
| `health.meals` absent from contracts.py | Medium | Meals data ingested in future will not appear in the badge strip or source-state API; follow-up D5 |
| `by-day` endpoint missing `privacy_tier` narrowing | Low | Default behaviour (exclude restricted) is correct; only optional narrowing absent |
| UTC-only day-close cache | Low | Operators in non-UTC timezones will see day boundaries at UTC midnight, not local midnight |
| Historical window lacks manual refresh button | Low | Data for historical windows does not change; user impact is cosmetic |

### Areas for human review

1. **`roster/chronicler/api/router.py`** — the day-close GET and POST refresh handlers; verify `tz` gap (D2) is acceptable for the near term.
2. **`src/butlers/chronicler/contracts.py`** — confirm `health.meals` absence is intentional gap vs. oversight (D5).
3. **`tests/chronicler/test_aggregation_no_cross_schema.py`** — the static SQL parser is the primary guardrail against cross-schema drift; review for completeness if new aggregate SQL is added.
4. **`frontend/src/components/chronicles/MapWidgetInner.tsx`** — OwnTracks trail rendering and sensitive-coordinate filter; verify filter covers all privacy_tier values.

---

## 8. Appendix

### All commits referencing bu-ig72b

Derived from `git log --oneline --all --grep='bu-ig72b'`:

| Commit | Summary |
|---|---|
| ed6c4f55 | docs: add-dashboard-chronicles reconciliation memo [bu-ig72b.37] (#1182) |
| cf209710 | feat(frontend): OwnTracks trail rendering on map [bu-ig72b.35] (#1176) |
| 6fa02c2c | feat(chronicler): OTel spans for aggregate and source-state handlers [bu-ig72b.17] (#1173) |
| 1aaa0b0d | docs: close RFC 0014 open question on dashboard surface [bu-ig72b.8] (#1174) |
| 13201089 | feat(chronicles): calendar location text-based map pan [bu-ig72b.24] (#1172) |
| e0a81a6b | docs: update §4a Chronicler row with new API routes [bu-ig72b.7] (#1171) |
| 3f857166 | feat(frontend): playhead binding [bu-ig72b.23] (#1170) |
| 71b0faae | feat(chronicles): episode drilldown drawer [bu-ig72b.31] (#1169) |
| 4b2f9c0b | feat(chronicles): loading/error/empty states [bu-ig72b.25] (#1168) |
| 090bba97 | refactor(chronicler): Gantt hover tooltip via Radix [bu-ig72b.30] (#1163) |
| b17dca2a | feat(chronicles): sensitive privacy_tier masking [bu-ig72b.29] (#1160) |
| 5a29ff3a | test(chronicler): consolidated no-LLM guardrail [bu-ig72b.15] (#1159) |
| bc566907 | test(chronicler): aggregate endpoint P95 latency benchmark [bu-ig72b.18] (#1154) |
| 442f9269 | feat: streak callouts panel [bu-ig72b.34] (#1153) |
| 03f38174 | feat(chronicler): POST /aggregate/day-close/refresh [bu-ig72b.26] (#1152) |
| f7822d87 | feat: Gantt swimlane component (overlap-aware) [bu-ig72b.28] (#1151) |
| 4666738a | feat(frontend): ChroniclesPage useAutoRefresh adoption [bu-ig72b.27] (#1149) |
| a2fbea36 | feat(chronicler): GET /api/chronicler/aggregate/day-close reader (#1144) |
| 5a5cbda7 | feat(chronicler): structured ErrorResponse envelopes [bu-ig72b.11] (#1147) |
| 24766a91 | feat: GET /api/chronicler/aggregate/by-category [bu-ig72b.9] (#1145) |
| 8af2831d | feat: MapWidget with OSM tiles, code-split maplibre-gl [bu-ig72b.14] (#1143) |
| ebf66f16 | feat(chronicler): GET /api/chronicler/aggregate/by-day [bu-ig72b.10] (#1141) |
| 98772962 | feat: Chronicles time-window picker with URL sync [bu-ig72b.20] (#1142) |
| 37144b2a | feat(chronicler): wire day-close cache writer [bu-ig72b.12] (#1140) |
| 2375a4c5 | feat(chronicler): GET /api/chronicler/source-state [bu-ig72b.2] (#1138) |
| 50c148c5 | feat: add tier2_cache migration [bu-ig72b.3] (#1137) |
| b8760c64 | chore(frontend): add maplibre-gl@^5 dependency [bu-ig72b.6] (#1136) |
| 8a4ae6d2 | feat(chronicler): add category_for() aggregation function [bu-ig72b.1] |
| (direct) | feat(frontend): register /chronicles route + ChroniclesPage shell [bu-ig72b.4] |
| (direct) | feat: add LANE_TAXONOMY constant [bu-ig72b.5] |
| (direct) | feat(frontend): Chronicles nav entry [bu-ig72b.13] |
| (direct) | feat: TanStack hooks for chronicles endpoints [bu-ig72b.21] |
| (direct) | feat: SourceStateBadgeStrip [bu-ig72b.22] |
| (direct) | feat: AggregatePieChart by-category [bu-ig72b.32] |
| (direct) | feat: stacked bar chart by-day × category [bu-ig72b.33] |
| (direct) | test(chronicler): no-cross-schema SQL guardrail [bu-ig72b.16] |

### Key new files introduced

**Backend:**
- `src/butlers/chronicler/aggregations.py` — `category_for()` mapping function
- `src/butlers/chronicler/day_close_writer.py` — tier2_cache write + staleness
- `src/butlers/migrations/versions/<tier2_cache>.py` — Alembic migration
- `roster/chronicler/api/router.py` — 5 new route handlers

**Frontend:**
- `frontend/src/pages/ChroniclesPage.tsx`
- `frontend/src/components/chronicles/GanttSwimlane.tsx`, `GanttSwimlaneInner.tsx`
- `frontend/src/components/chronicles/MapWidget.tsx`, `MapWidgetInner.tsx`
- `frontend/src/components/chronicles/SourceStateBadgeStrip.tsx`
- `frontend/src/components/chronicles/EpisodeDrawer.tsx`
- `frontend/src/components/chronicles/AggregatePieChart.tsx`
- `frontend/src/components/chronicles/AggregateStackedBar.tsx`
- `frontend/src/components/chronicles/StreakCallouts.tsx`
- `frontend/src/hooks/use-chronicles.ts`
- `frontend/src/hooks/use-time-window.ts`
- `frontend/src/components/chronicles/lane-taxonomy.ts`

**Tests:**
- `tests/chronicler/test_aggregation_no_llm.py`
- `tests/chronicler/test_aggregation_no_cross_schema.py`
- `tests/chronicler/test_aggregation_latency.py`
- `tests/chronicler/test_aggregate_by_category.py`
- `tests/chronicler/test_aggregate_by_day.py`
- `tests/chronicler/test_day_close_reader_api.py`
- `tests/chronicler/test_day_close_refresh_api.py`
- `tests/chronicler/test_source_state_api.py`
- `tests/chronicler/test_aggregate_spans.py`

### Source reliability notes

Sections 2 (Architecture) and 3 (Spec Compliance Matrix) are built from first-hand
inspection of spec files + reconciliation memo.
Section 4 (Performance) is sourced from PR #1154 and PR #1143 body text.
Section 5 (Sibling status) is sourced from `bd show <id>` for each sibling bead.
The reconciliation memo (`docs/reports/2026-04-26-add-dashboard-chronicles-reconciliation.md`,
produced by bu-ig72b.37 / PR #1182) was the primary input for the drift analysis
in sections 1 and 6.
