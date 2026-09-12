## Why

The Decisions sidebar badge currently turns both a readable empty digest and an
unavailable or failed digest into the same calm no-badge state. The Decisions
page already distinguishes those states, so navigation can hide a source
failure before the owner reaches the page.

## What Changes

- Replace the Decisions badge's count-only hook result with a narrow,
  discriminated Decisions badge state that distinguishes availability from a
  positive count.
- Render an accessible unavailable marker for a degraded digest or direct
  query error in the rail, expanded desktop sidebar, and mobile sidebar.
- Preserve the quiet no-badge state for a readable empty digest and the
  existing numeric badge for positive counts.
- Update the Dashboard Decisions capability requirement and focused frontend
  tests only.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-decisions`: Sidebar navigation must distinguish an unavailable
  Decisions digest from a successfully empty one without adding mutation
  authority or changing the Decisions API.

## Impact

- Affected code: `frontend/src/hooks/use-qa-badge.ts`,
  `frontend/src/components/layout/Sidebar.tsx`, and their focused tests.
- Affected contract: `openspec/specs/dashboard-decisions/spec.md`.
- No API, model, route, Beads, Dolt, runtime bridge, or backend changes.
