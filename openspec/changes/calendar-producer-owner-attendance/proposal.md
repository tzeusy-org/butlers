## Why

The General calendar producer asserts `meeting`/`focused` from any active
confirmed human event. It filters provenance only, so an event the owner
declined, or one marked free, can hold a `meeting` signal at confidence 1.0
and make routine insights look held for a meeting the owner will not attend.
The conflict radar and day load already apply the shared attendance rule
(`counts_toward_owner_load`); the producer did not.

This is the attendance slice of bu-s11n0s.4. The typed Google event-type
slices (persisted `event_type`/working location, out-of-office `away`, typed
focus, working-location signal, and day-load/prep exclusions) remain open on
that bead.

## What Changes

- The calendar producer applies `counts_toward_owner_load` to each candidate:
  an owner-declined or transparent event cannot assert context, and an
  overlapping attended opaque event wins.
- Guest declines, owner tentative/needsAction answers and absent or malformed
  attendee metadata stay eligible (existing helper semantics).

## Capabilities

### Modified Capabilities

- `context-bus`: Calendar Producer Provenance Candidate Selection gains the
  owner-attendance exclusion and overlap rule.

## Impact

- Code: `src/butlers/jobs/context_producers.py`.
- Tests: `tests/jobs/test_context_producers.py`.
- No schema, migration, provider, or API change.
