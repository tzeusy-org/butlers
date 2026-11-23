## Why

Two surfaces can tell the owner a day is clear when the system cannot know. The dashboard day
briefing renders "Tomorrow is clear" for a failed read, and the 23:00 Telegram tomorrow-prep is a
model listing one provider calendar with no RSVP filter, hard-coded SGT, and an auth error that
reads as "No events scheduled for tomorrow" (bu-5ii4gj.2, calendar pursuit run 26 move 2).

## What changes

- One pure projection, `build_day_sheet`, answers "what is this local day" for every surface: the
  owner-time items in owner-local order, the free gaps, the attended load, and a freshness state of
  `current`, `stale` or `unreadable`.
- One fixed-template renderer, `render_day_sheet_text`, produces the Telegram text. No model composes
  or rewrites it, and third-party titles are defanged.
- An unreadable or stale read is rendered as such, never as a clear day. Empty is only said when the
  read is current.

## Slices

- S1 (this change's first PR): the pure module and its tests.
- S2: `GET /api/calendar/workspace/day-sheet` and the dashboard hook.
- S3: `day_sheet_push` loop job, dark behind a flag, and the `eod-tomorrow-prep` prompt loses its
  event enumeration (a butler-general delta lands with it).
- S4: the Overview strip.

## Decisions

- Gaps are computed inside a day window, the named constant `DEFAULT_WINDOW` (08:00 to 20:00
  owner-local), because no working-hours setting exists yet. An engineering default.
- A transparent event is omitted entirely, matching the owner-time predicate (the behaviour matrix's
  "shown if owner-authored" needs an authorship fact the projection does not store).
