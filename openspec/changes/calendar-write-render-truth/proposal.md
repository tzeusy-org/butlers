## Why

The calendar reports success or calm it cannot back (bu-5ii4gj.4, calendar pursuit run 26 move 4).
A Butlers block created or moved over a primary-calendar meeting reports no conflict, because the
overlap check reads only the calendar the block lands on, which is not where the owner's meetings
live.

## What changes

- Slice A: a write's overlap check spans all owner time (the target calendar plus every source
  whose stored role is `owner` or `butler`), never another person's shared calendar, and refuses
  with "conflict check unavailable" when that time cannot be read.

## Deferred

- Slice C: failed proposals and overlay reads render an error state, not an empty one.
- Slice B: mirror-delete failure handling, butler-authored task mirrors, fleet-wide
  `calendar_set_primary`.
- Slice B2: no standing rule for an `always_per_event` tool.
- Slice D: span-true grid with counted suppression and truncation.
