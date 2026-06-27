## Context

This is a **spec-only change**. The implementation already ships in:

- `src/butlers/api/routers/calendar_workspace.py` — find-time handler (lines 2750–2824)
  wraps the MCP call in a try/except and returns `available=False, reason=...` on
  any `HTTPException`; the outer `find_time` route always returns HTTP 200.
- `src/butlers/api/read_models/calendar_workspace_v1.py` — `query_calendar_event_search`
  (lines 1183–1252) collects per-schema results; sets `available = any(ok for _, _, ok in results)`.
  `CalendarSearchResults.available` defaults to `True`; is set `False` only when
  `any_ok` remains `False` after the fan-out (every schema errored).

No new code is written here. The design task is to confirm the right spec location
and scenario framing to prevent future drift.

## Goals / Non-Goals

**Goals:**
- Record the find-time fail-open contract in `dashboard-api` "Calendar Workspace".
- Record the search degraded-signal semantics (`available=False` ↔ all schemas failed)
  in both `dashboard-api` "Calendar Workspace" and `module-calendar` "Calendar Event
  Full-Text Search Query".
- Reference the implementing code paths so a future reader can verify alignment.

**Non-Goals:**
- No code change, no migration, no frontend wiring.
- No change to the `available` field type or presence in the API response shape
  (already present in `CalendarWorkspaceFindTimeResponse` and
  `CalendarWorkspaceSearchResponse`).

## Decisions

**D1: MODIFIED (not ADDED) for both spec deltas.**
The requirements being updated exist in the canonical specs already. Scenarios are
added to existing requirements rather than creating new requirements, so the MODIFIED
operation is correct. The full requirement text is reproduced (per OpenSpec convention)
to avoid partial-content drift at archive time.

**D2: Degraded scenario placed in both `dashboard-api` and `module-calendar`.**
- `dashboard-api` is the HTTP contract owner for the endpoint (HTTP 200, `available`,
  `reason`). UIs read it here.
- `module-calendar` is the projection/search contract owner. It clarifies `available`
  at the fan-out layer so that future module implementers understand the semantics
  independent of any HTTP framing.
Keeping both in sync avoids a gap where the API spec says "available=false = all fail"
but the module spec is silent on when that happens.

## Risks / Trade-offs

[Risk: Spec describes code that has not yet been tested under total-failure conditions]
→ Mitigation: the `available=false` path for search is covered by the docstring in
`CalendarSearchResults`; for find-time the `except HTTPException` block is explicit.
Follow-up integration tests are tracked separately.

## Open Questions

None — the implementation is complete and the spec merely documents it.
