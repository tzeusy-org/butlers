## Why

When an organizer moves, shortens or cancels a meeting the owner already planned around, nothing on
`/calendar` says so: the grid just shows the new time. bu-5ii4gj.11 (calendar pursuit run 26, track
N) asks for those moved-under-you changes to appear in the Needs-you queue with what changed.

## What changes

- **A changes read.** `GET /api/calendar/workspace/changes?since=` returns the organizer-made
  changes to start, end, status or location since a cursor, each with the prior and new values and
  the event ids as evidence. It is derived from the connector's revision journal
  (`connectors.calendar_event_revisions`); it adds no store.
- **A narrow journal reader.** The dashboard cannot read the journal, so a fixed-search-path
  `SECURITY DEFINER` function returns only the fields the feed needs, capped, the same way
  `public.read_calendar_event_heads` serves the drift job.
- **A queue kind.** The Needs-you queue shows each change as an item with its before and after, and
  stops saying changes are not tracked.

## Out of scope

A persisted dismiss or acknowledgement of a change (bu-5ii4gj.11.2, with the other `x` verbs); the
cursor is held by the client. Attendee-list changes, description and conference changes, and
anything the owner did themselves, and events the owner has declined. No notification: the queue is the surface.

## Slices

N3a this spec; N3b the definer migration and the route; N3c the frontend kind.
