## Why

The calendar find-time and search endpoints were implemented with explicit fail-open
behaviour (commit cbac98bdb via PR #2658), but the degraded-envelope contract was
never written into the canonical specs. The canonical `dashboard-api` "Calendar
Workspace" requirement still lacks: (a) the `POST /api/calendar/workspace/find-time`
endpoint entry, (b) its `available=false` + `reason` degraded scenario, and
(c) the search degraded-signal rule (`available=false` only when **every** targeted
schema fails). The `module-calendar` "Calendar Event Full-Text Search Query"
requirement already documents per-schema trgm fallback but is silent on what the
result-level `available` flag means when failures are partial vs total. This delta
locks the contract now that the implementing changes are archived and canonical.

## What Changes

- **Add `POST /api/calendar/workspace/find-time` to the `dashboard-api` "Calendar
  Workspace" endpoint table.** The endpoint exists in code but was absent from the
  spec table.
- **Add a find-time degraded-envelope scenario** to `dashboard-api` "Calendar
  Workspace": when the MCP free/busy call raises any error the endpoint SHALL return
  HTTP 200 with `available=false` and a human-readable `reason`; never 500.
- **Add a search degraded-signal scenario** to `dashboard-api` "Calendar Workspace":
  `available=false` is set in the search response ONLY when every targeted schema
  fails to respond; a partial failure (some schemas succeed) still yields results
  with `available=true`.
- **Add an `available` signal rule** to `module-calendar` "Calendar Event Full-Text
  Search Query": the same partial-vs-total semantics for the projection-layer search
  function (`query_calendar_event_search`).

## Capabilities

### New Capabilities

_None — this is a spec-only delta documenting already-shipped behaviour._

### Modified Capabilities

- `dashboard-api`: "Calendar Workspace" requirement gains the find-time endpoint
  entry and two degraded scenarios (find-time fail-open; search partial-failure rule).
- `module-calendar`: "Calendar Event Full-Text Search Query" requirement gains an
  explicit `available` signal rule (False only when ALL schemas fail).

## Impact

- **No code changes.** The behaviour described here already exists in
  `src/butlers/api/routers/calendar_workspace.py` (find-time handler lines 2803–2824;
  search handler line 1686) and
  `src/butlers/api/read_models/calendar_workspace_v1.py`
  (`CalendarSearchResults`, `query_calendar_event_search` lines 1244–1252).
- **Spec files touched:** `openspec/specs/dashboard-api/spec.md` and
  `openspec/specs/module-calendar/spec.md`.
- **No migration, no frontend change, no API shape change.** The `available` field
  already exists in `CalendarWorkspaceSearchResponse` and
  `CalendarWorkspaceFindTimeResponse`.
