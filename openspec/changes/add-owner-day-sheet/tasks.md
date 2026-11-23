## 1. Slice 1: the pure projection

- [x] 1.1 `butlers.core.temporal.day_sheet`: `build_day_sheet`, `render_day_sheet_text`, digest.
- [x] 1.2 Unit tests over declined, transparent, butler, all-day, cross-midnight, DST, unreadable, stale and empty-but-current.

## 2. Later slices

- [x] 2.1a `GET /api/calendar/workspace/day-sheet` and read model (S2).
- [x] 2.1b Dashboard `useDaySheet` hook; the day-briefing card no longer says "Tomorrow is clear" (S4).
- [x] 2.2 `day_sheet_push` and the tomorrow-prep prompt change, with a butler-general delta (S3).
- [x] 2.3 Overview strip (S4).

## 3. Deferred

- [ ] 3.1 The `claim` item kind (what needs the owner) and a `basis` field on the sheet.
