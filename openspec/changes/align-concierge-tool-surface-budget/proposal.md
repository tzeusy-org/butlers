## Why

The Concierge capability spec and its roster integration test still interpret
RFC 0002's former 30-50 tool target as a ceiling on registered MCP handlers.
Accepted RFC 0002 Amendment 1 and RFC 0027 instead apply that target to full
definitions initially loaded into model context while keeping canonical
FastMCP `tools/list` complete. The stale requirement could otherwise encourage
removal of legitimate role-fit tools merely to satisfy a presentation budget.

## What Changes

- Define Concierge registration in terms of its complete role-fit core and
  module surface, including the exact `dashboard_read` tool set.
- Treat canonical registered-tool enumeration as registration evidence, not as
  evidence of the initial model working set or its schema bytes.
- Preserve core/module group selection, type/name gates, module state,
  dashboard-read docstrings, and source envelopes.
- Leave runtime Tool Search, deferred loading, initial-schema-byte measurement,
  and admission under the existing `bu-ondtw` delivery lane.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `butler-concierge`: Align the staffer's tool-surface budget requirement with
  the accepted distinction between complete registration and initial model
  presentation.

## Impact

- `roster/concierge/tests/test_dashboard_read.py`
- `openspec/specs/butler-concierge/spec.md` after this delta is eventually
  synced or archived
- No runtime handler, roster module selection, tool group, discovery policy,
  persistence, authorization, or deployment change
