## Why

The workspace overwrites an event's authoring zone with the display zone, so a 09:00 London call
shows as 04:00 in another zone with nothing marking it as foreign (bu-5ii4gj.5, run 26 move 5).

## What changes

- Each workspace entry keeps the IANA zone the event was authored in (`source_timezone`) beside the
  display `timezone`; an invalid or missing zone is `null`, never guessed.
- The grid, agenda and detail panel show the event's own clock beside the displayed one when the
  zones differ.

## Not in this change

The meeting card, prep and debrief reading the owner zone, and spaced-repetition schedule zones, are
fixes to existing behavior and ship with their own tests. The zone switch in the toolbar, projected
owner zone for future days and typed schedule anchors are later slices of the same bead.
