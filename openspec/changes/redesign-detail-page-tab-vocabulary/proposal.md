## Why

Decision gate B (`bu-41p8z`) resolved the conflict between the current
10-tab operator contract and the Dispatch redesign's narrower 7-tab resident
vocabulary by choosing **B2: operator/resident mode toggle**. The spec needs to
encode that decision before frontend work rewrites the butler detail tab shell.

## What Changes

- Modify the Butler detail tab requirements so `/butlers/:name` defaults to
  resident mode with the narrow Dispatch vocabulary: Overview, Activity, Logs,
  Approvals, Spend, Config, Memory.
- Preserve the full 10 spec-mandated tabs in operator mode: Overview,
  Sessions, Config, Skills, Schedules, Trigger, MCP, State, CRM, Memory.
- Require the selected mode to persist in `localStorage` using
  `butlers.detail.mode`, defaulting to `resident`.
- Require deep links to mode-exclusive tabs to resolve the owning mode before
  fallback; operator-only tabs auto-promote the page to operator mode rather
  than falling back to Overview.
- Preserve conditional tabs across both modes: switchboard Routing Log and
  Registry, health Health, general Collections and Entities, and education
  Reviews.
- Explicitly handle the current non-spec Models tab: while current code exposes
  it, Models is operator-only and does not count toward the 10 spec-mandated
  base tabs.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `dashboard-butler-management`: Butler detail tab visibility, mode persistence,
  deep-link semantics, conditional-tab preservation, and treatment of the
  current non-spec Models tab.

## Impact

- **Frontend**: `frontend/src/pages/ButlerDetailPage.tsx` tab configuration,
  mode toggle, URL handling, localStorage persistence, conditional-tab assembly,
  and tests.
- **Spec**: `dashboard-butler-management` gains B2 operator/resident vocabulary
  rules before implementation beads update the page.
- **No API, database, migration, or backend runtime changes.**
