## 1. P1: briefing date follows the local day

- [x] 1.1 `useLocalDayStart` re-evaluates at local midnight (re-armed at most every minute) and on focus, pageshow and visibility
- [x] 1.2 The day-briefing target date derives from it instead of a mount-time memo
- [x] 1.3 Tests: midnight rollover, stable identity within a day, hidden tab resuming, focus and pageshow after a clock jump, the 60s cap

## 2. P2: day verdict

- [x] 2.1 `build_day_verdict` over the day sheet, `query_calendar_day_verdict` and `GET /api/calendar/workspace/day-verdict` with `available`
- [x] 2.2 Opener clauses: next commitment, committed hours, longest free window, each a door; the 'Tomorrow · date' label derives from the verdict date so the 18:00 flip is labelled truly
- [x] 2.3 Tests: seeded schema with tentative, declined and all-day rows; forced query failure

## 3. P3: owner.agenda on /now

- [x] 3.1 `owner.agenda` reducer (`perception/agenda.py`) and fixtures: in meeting, free, none left, declined and transparent ignored, unreadable, stale. Read at request time from the day verdict, not a store slot: a slot kind needs a privileged migration and source registry, tracked separately
- [x] 3.2 Agenda line appended by `/api/now` with `door` and `fresh_until` fields; per-process last-known value
- [x] 3.3 `useNow`, the shell strip, the Overview link and `['now']` invalidation on `calendar` events
