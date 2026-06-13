## Why

The Dispatch redesign makes the Butler detail Overview tab the dense identity
surface for `/butlers/:name`, replacing the old mixed Overview of identity,
module health, cost, eligibility, and recent notifications with a deliberate
card stack. This depends on the now-merged `redesign-butler-detail-no-hero`
change, which keeps identity inside the Overview tab rather than a Tier 2 Hero
(`openspec/changes/redesign-butler-detail-no-hero/proposal.md:17-19`,
`openspec/changes/redesign-butler-detail-no-hero/specs/dashboard-butler-management/spec.md:10-18`).

It also depends on the now-merged `add-butler-process-facts` change, which
defines the Overview process facts card, rejects `pid`, and specifies the
backend detail-surface fields for `container_name`, `port`,
`registered_duration_seconds`, and `config_path`
(`openspec/changes/add-butler-process-facts/proposal.md:7-10`,
`openspec/changes/add-butler-process-facts/specs/dashboard-butler-management/spec.md:3-38`).

## What Changes

- Modify the existing Butler Overview Tab requirement to enumerate seven
  ordered card-stack units:
  1. identity card
  2. process facts card
  3. heartbeat row
  4. module health card
  5. cost card
  6. recent sessions card
  7. eligibility row
- Preserve the existing eligibility behaviors that survive the redesign:
  active/quarantined/stale badge semantics, restore mutation, quarantine reason,
  24-hour eligibility timeline, tooltip labels, and 60-second refresh cadence.
- Replace the old recent notifications feed requirement with a recent sessions
  card because Epic 04 explicitly adapts the Dispatch card stack to existing
  session data hooks rather than notification feed content.
- Keep the Overview stack source-backed by existing hooks/endpoints instead of
  introducing mock-only fields.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-butler-management`: Reorganizes the Butler detail Overview tab into
  a seven-unit card stack and pins each card to its source hook or endpoint.

## Impact

- **Frontend implementation**: `frontend/src/components/butler-detail/ButlerOverviewTab.tsx`
  should become a card-stack layout consuming `useButler`,
  `useButlerHeartbeats`, `useCostSummary`, `useButlerSessions`, registry
  eligibility hooks, and module-health data on the butler detail response.
- **Backend/API contracts**: No new API beyond the sibling process-facts change.
  The stack consumes the existing butler detail endpoint, system heartbeat
  endpoint, cost summary endpoint, sessions endpoint, and switchboard registry
  eligibility APIs.
- **No database migration required**: this is a presentation and contract
  reorganization over existing or already-specified data surfaces.

## Source References

- `useButler` drives identity/detail data (`frontend/src/hooks/use-butlers.ts:26-33`).
- Process facts are defined by `add-butler-process-facts`
  (`openspec/changes/add-butler-process-facts/specs/dashboard-butler-management/spec.md:3-38`).
- System heartbeat data comes from `GET /api/system/butlers/heartbeat`
  (`src/butlers/api/routers/system.py:639-699`) and the frontend
  `useButlerHeartbeats` hook (`frontend/src/hooks/use-system.ts:71-78`).
- Module health is obtained through `_get_module_health_via_mcp` and the
  `/api/butlers/{name}/modules` path (`src/butlers/api/routers/butlers.py:549-670`).
- Cost telemetry uses `useCostSummary` (`frontend/src/hooks/use-costs.ts:31-47`).
- Recent sessions use `useButlerSessions` (`frontend/src/hooks/use-sessions.ts:27-35`).
- Eligibility uses the existing Overview requirement
  (`openspec/specs/dashboard-butler-management/spec.md:212-225`) and frontend
  registry/eligibility hooks (`frontend/src/hooks/use-general.ts:24-53`).
