## Why

The Overview currently turns direct cost-summary and top-sessions query failures into the
same values used for successful zero-cost and empty-session results. That can make an
unavailable cost source look like a truthful `$0.00`, a top-butler claim, or a calm empty
sessions message.

## What Changes

- Preserve direct cost-summary query failure as explicit unavailable state from
  `DashboardPage` through `CostWidget`.
- Preserve direct top-sessions query failure as explicit unavailable state from
  `DashboardPage` through `TopSessionsTable`.
- Keep successful payloads carrying `source_error` as their existing degraded state.
- Keep successful zero-cost summaries and successful empty session lists as calm states.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-domain-pages`: Overview cost readers distinguish direct request failure,
  successful degraded envelopes, and successful calm data.

## Impact

- Affects `DashboardPage`, `CostWidget`, `TopSessionsTable`, and their focused Vitest
  coverage.
- Does not change dashboard API contracts, cost hooks, query keys, retry behavior, or the
  Spend page.
