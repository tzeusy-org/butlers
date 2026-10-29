# Explicit Calendar response group and worker-free startup

## Why

Messenger needs its approval-bound invitation response capability without the
Calendar CRUD surface or discovery/projection workers. The accepted
`bu-qozovd.1` slice 2 isolates startup and registration while retaining both
historical response owners and their evidence reads.

## What Changes

- Add an explicit `response` tool group; omitted/empty groups retain all groups.
- Configure Messenger response-only and Relationship core plus response.
- Resolve canonical credentials and construct the lazy provider for response-only
  startup, then skip calendar discovery, creation, target persistence, source
  hygiene, queue recovery and all Calendar background workers.
- Keep exact account/source admission, owning approval/preparer/executor,
  participant-only provider effect, successful-effect projection and both
  historical fence/status contracts unchanged.

## Capabilities

### Modified Capabilities

- `module-calendar`: explicit tool groups and their startup boundaries; qualify
  normal-startup discovery and full-surface guarantees without changing
  REQ-module-calendar-029/030 or REQ-dashboard-api-069.

## Impact

Calendar lifecycle/metadata/response registrar, two roster configurations,
Calendar docs and existing response/surface/catalog tests. No scheduler,
registry, API, migration, fence protocol or generic mutation runner changes.
Original six acceptance criteria and later slices remain open and unchanged.
