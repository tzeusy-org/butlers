## Why

The butler status board can presently claim a healthy fleet while rows are
overdue or their liveness is unknown, and it leaves status-board chrome around
an initial load failure with no board data to contextualize it. Its cadence
copy also calls two-hour and other noncanonical schedules “hourly,” which makes
the operator-facing explanation quantitatively false.

## What Changes

- Suppress the status-board header and footer only for an initial board failure
  with no cached rows; keep both chrome and the stale-data warning when cached
  rows remain available.
- Make the healthy total exclude every non-healthy canonical activity:
  `offline`, `quarantined`, `overdue`, and `unknown`.
- Publish an `unknown` board aggregate derived from canonical row activity,
  rather than treating registry eligibility unavailability as a liveness
  substitute.
- Reserve the `hourly`, `daily`, and `weekly` cadence labels for their canonical
  intervals; expose noncanonical intervals, including two hours, as `custom`.
- Add focused API and dashboard regression coverage for the truthfulness rules.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-butler-management`: status-board chrome, fleet-health arithmetic,
  and cadence-label semantics become explicit fail-closed operator contracts.

## Impact

- Backend: `GET /api/butlers/board` cadence-label helper.
- Frontend: the row-derived board aggregate, header arithmetic, and the page
  shell’s error-state composition.
- Tests: focused board API and status-board page, header, and hook coverage.
- No migration, schedule execution policy, registry eligibility policy, or
  cached-row mutation is introduced.
