## Why

The session-detail documentation still describes a retired butler-scoped lookup
even though production resolves every session through the global
`GET /api/sessions/{id}` fan-out. Separately, the optional trigger breakdown
can be partial when its own fan-out loses a pool, yet the dashboard may present
its top trigger as a complete dominant-cluster finding.

## What Changes

- Correct the dashboard session-detail contract, API reference, and data-flow
  diagram to name the global detail route as authoritative.
- Define tolerated legacy `/sessions/{id}?butler=<name>` links as ignored input:
  they remain navigable, but do not select a second API route or alter the
  global lookup.
- Add an opt-in aggregate data signal, `trigger_breakdown_degraded_sources`,
  for pools that fail only the trigger-breakdown fan-out. Retain the scalar
  aggregate's existing `meta.sources_degraded` signal unchanged.
- Require the sessions verdict to retain truthful scalar failure counts while
  withholding a trigger-dominance attribution when the trigger breakdown is
  partial.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `dashboard-api`: Define the global session-detail route and the distinct
  trigger-breakdown degradation signal on the existing aggregate response.
- `dashboard-visibility`: Define ignored legacy session-detail query state and
  suppress unsupported trigger-dominance wording in the sessions verdict.

## Impact

- Backend aggregate read model, response DTO, and sessions router.
- Frontend aggregate type and sessions verdict opener.
- Session-detail tests, aggregate tests, dashboard API contract, dashboard
  visibility spec, and the editable/rendered dashboard data-flow diagram.
- No database migration, endpoint addition, owner-policy change, or broad
  dashboard redesign.
