## Why

The calendar opener is a tally, not a verdict about the day: it names sync freshness, a conflict
count and unanswered invitations, so a packed week with no conflicts reads as "Quiet week". The
owner's next commitment, committed hours and longest free window appear nowhere on the dashboard, and
the deterministic `/now` card has no agenda line and no consumer (bu-5ii4gj.2, run 26 move 2).

## What changes

- A deterministic day verdict read model in the calendar workspace, reading deduplicated workspace
  rows and the existing free-slot computation: next commitment, committed minutes and the longest
  free window, each clause carrying the entry it came from. It replaces the tally in the opener.
- A situation kind `owner.agenda` on `/now`: in a meeting until HH:MM with the next start, free
  until HH:MM, none left today, or unknown with the last known value.
- The briefing target day follows the owner's local clock instead of freezing at page mount.

## Already true on main

- `/day-briefing` fails closed: `overlays_available=false` on a view or query failure, and the card
  renders an unavailable note, never a clear day (#6092). Pinned by existing tests.
- The Overview day-sheet strip and the `/day-sheet` read model shipped (#6024).

## Slices

- P1: the briefing date follows the local day (frontend), plus this change.
- P2: `query_calendar_day_verdict`, its route and the opener clauses.
- P3: the `owner.agenda` producer, `/now` rendering, the shell and Overview strip, and `['now']`
  invalidation on calendar bus events.
