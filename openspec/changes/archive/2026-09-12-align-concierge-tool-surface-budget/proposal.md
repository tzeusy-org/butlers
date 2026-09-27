## Why

The Concierge capability spec and roster integration test, its manifesto, and
the task-continuity contract commentary still interpret RFC 0002's former
30-50 tool target as a ceiling on registered MCP handlers. Accepted RFC 0002
Amendment 1 and RFC 0027 instead apply that target to full definitions
initially loaded into model context while keeping canonical FastMCP
`tools/list` complete. The stale claims could otherwise encourage removal of
legitimate role-fit tools merely to satisfy a presentation budget.

## What Changes

- Define Concierge registration in terms of its complete role-fit core and
  module surface, including the exact `dashboard_read` tool set.
- Treat canonical registered-tool enumeration as registration evidence, not as
  evidence of the initial model working set or its schema bytes.
- Preserve core/module group selection, type/name gates, module state,
  dashboard-read docstrings, and source envelopes.
- Preserve `carry_forward`'s independent staffer role-fit and reachability
  rationale while removing its false registered-handler-budget rationale.
- Leave runtime Tool Search, deferred loading, initial-schema-byte measurement,
  and admission under the existing `bu-ondtw` delivery lane.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `butler-concierge`: Align the staffer's tool-surface budget requirement with
  the accepted distinction between complete registration and initial model
  presentation.
- `core-scheduler`: Align the task-continuity registration rationale with the
  same distinction without changing staffer gating.

## Impact

- `roster/concierge/tests/test_dashboard_read.py`
- `roster/concierge/MANIFESTO.md`
- `src/butlers/core_tools/_continuity.py`
- `tests/core_tools/test_continuity.py` (module commentary only)
- `openspec/specs/butler-concierge/spec.md` after this delta is eventually
  synced or archived
- `openspec/specs/core-scheduler/spec.md` after this delta is eventually synced
  or archived
- No runtime handler, roster module selection, tool group, discovery policy,
  persistence, authorization, or deployment change
