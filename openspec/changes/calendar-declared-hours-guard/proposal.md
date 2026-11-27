## Why

The owner declares meeting hours and days (earliest and latest time, meeting days, no-meeting
blocks) and the scheduling tools already honour them when they pick a slot. An invitation or an
accepted event that lands outside them is never flagged, so the owner finds out by accepting it.
bu-5ii4gj.9 (calendar pursuit run 26 move 9) asks that the same declaration flag such events in the
radar and on the invitations inbox, before the owner answers.

## What changes

- A fourth radar issue kind, `outside_declared_hours`, one issue per event, with a reason
  (`before_earliest`, `after_latest`, `off_day`, `no_meeting_block`).
- It is judged by the shared scheduling-preference check, in the owner's declared zone, for timed
  opaque events the owner attends (accepted, tentative or not yet answered). Declined, free,
  all-day, butler-generated and declared-status (out of office, working location, focus) events are
  never flagged. A quarantined invitation holds no time and has no flag.
- Severity: a warning when the owner has not answered or the event repeats, information otherwise.
- The invitations inbox carries the flag like any other radar issue on its entry, and the
  overcommitment producer qualifies the warning kind as a condition.
- No migration, no new tool, no provider call.

## Out of scope

Learned routine hours, travel-zone inference and any answer to the invitation: the flag only
informs. The docket of prepared answers is a separate change.
