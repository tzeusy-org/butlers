## 1. Backend: per-trip degraded envelope

- [x] 1.1 Add `unreadable_trip_ids: list[str] = []` to `UpcomingTravelModel`
  (`roster/travel/api/models.py`).
- [x] 1.2 In `get_upcoming_travel`, wrap each trip's row conversion and nested
  legs/accommodations fetch in a `try/except`; on failure, log a warning,
  append the trip id to `unreadable_trip_ids`, and skip it instead of raising.
- [x] 1.3 Add a router test: one trip with unreadable metadata among otherwise
  normal trips returns 200 with the good trip present and the bad trip's id in
  `unreadable_trip_ids`.

## 2. Frontend: honest KPI strip

- [x] 2.1 Add `isError: boolean` to `KpiStripProps`; wire it from
  `useUpcomingTravel(90)`'s `isError` in `ButlerTravelTripsTab`.
- [x] 2.2 On `isError`, render "unavailable" (never a numeral) for next
  departure / active / planned / open-actions cells and show a
  `SourceDegradedNote` banner naming `/api/travel/upcoming` with a retry
  action wired to `refetch()`.
- [x] 2.3 When not erroring but `unreadable_trip_ids.length > 0`, show a
  second `SourceDegradedNote` disclosing the excluded count.
- [x] 2.4 Component tests: `isError` renders no numerals and shows the
  banner; a non-empty `unreadable_trip_ids` renders the exclusion disclosure
  while active/planned counts still reflect the readable trips.

## 3. Verification and handoff

- [x] 3.1 Validate the OpenSpec change strictly and run the required lint,
  format, and test gates.
- [x] 3.2 Commit, push, and open a PR referencing bu-2jtfw.1.
