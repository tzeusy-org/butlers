## Why

The calendar workspace drops repeated-title events without saying so: a title repeated more than 20
times on one day is removed from the response with no count, and `has_more` is computed after the
removal, so a page that loses rows can claim nothing is left. The client also stops after 20 pages
and reports the window as complete (bu-5ii4gj.4, slice D, backend first).

## What changes

- The workspace read returns `suppressed_groups` (title, day, count, entries) instead of dropping
  rows silently, and derives `has_more` and the cursor before suppression.
- The page renders a counted chip that expands to the hidden entries, and says so when the page cap
  ended the fetch with rows still behind it.

## Already true on main

- The window query selects by interval overlap (`starts_at < end AND ends_at > start`), so a Fri
  22:00 to Sat 02:00 event is returned for a Saturday-only window. A test now pins it.

## Deferred

- Slice D2: bucket multi-day and cross-midnight events on every day they cover, column packing for
  concurrent events, continuation markers (frontend only).
