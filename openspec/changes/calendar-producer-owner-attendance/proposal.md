## Why

The General calendar producer asserts `meeting`/`focused` from any active
confirmed human event. It filters provenance only, so an event the owner
declined, or one marked free, can hold a `meeting` signal at confidence 1.0
and make routine insights look held for a meeting the owner will not attend.
The conflict radar and day load already apply the shared attendance rule
(`counts_toward_owner_load`); the producer did not.

This is the attendance slice of bu-s11n0s.4. The typed Google event-type
slices (persisted `event_type`/working location, out-of-office as calendar
context, typed focus, working location, and day-load/prep exclusions) are
delivered on that bead.

Working location is not a context signal. The declaration is persisted
(core_341) and readable through a typed read (`current_working_location`).
Publishing it as a signal is deferred to a perception kind
(`owner.work_location`) at the M7/.21 switch, so no writer is added to the
retiring context bus.

Out-of-office is calendar context only (bu-u33sb7): it suppresses `focused`,
never asserts `meeting` or `focused` itself (an attended overlapping meeting
keeps `meeting`), adds no day-load hours, is never a prep or starting-soon candidate,
and neither enters an owner mode nor writes the legacy `away` signal (which
maps to `owner.place`). bu-u33sb7 limits automatic mode entry to asleep, in
flight and house unattended; an out-of-office block can reach an owner mode
only through an owner-confirmed M7 proposal.

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

- Code: `src/butlers/jobs/context_producers.py`; the working-location read in
  `src/butlers/modules/calendar/working_location.py` (dark, no caller).
- Tests: `tests/jobs/test_context_producers.py`,
  `tests/modules/test_calendar_working_location.py`.
- No schema, migration, provider, or API change.
