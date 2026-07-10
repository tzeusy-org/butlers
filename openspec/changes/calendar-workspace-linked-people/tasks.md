## 1. Backend: batch linked-people resolver

- [x] 1.1 Add `query_calendar_entry_people(db, event_ids, butlers)` +
  `CalendarEntryPerson` / `CalendarEntryPeopleResult` DTOs to
  `src/butlers/api/read_models/calendar_workspace_v1.py`. One batch join per
  targeted schema over `calendar_event_entities` ⨝ `public.entities`, keyed on
  `event_id`; `fan_out_with_status` drives the degraded flag. NULL name → the
  caller maps to `"Unknown"`.
- [x] 1.2 Unit tests (mocked pool): hydration, missing-entity → "Unknown",
  degraded flag on resolution failure.
- [x] 1.3 Real-Postgres integration test for the JOIN: `$1::uuid[]` scoping,
  `public.entities` resolution + ordering, `LEFT JOIN` NULL-name row survives
  (`tests/api/test_calendar_workspace_linked_people_integration.py`).

## 2. Backend: models + router wiring

- [x] 2.1 Add `CalendarLinkedPerson`; `UnifiedCalendarEntry.linked_people` and
  `CalendarWorkspaceReadResponse.people_source_available` (additive) in
  `src/butlers/api/models/calendar_workspace.py`.
- [x] 2.2 `get_workspace` resolves people for the returned page's events (scoped
  to their producing schemas) and attaches them per entry; sets
  `people_source_available`.

## 3. Frontend: hydration

- [x] 3.1 Additive types `CalendarLinkedPerson`,
  `UnifiedCalendarEntry.linked_people?`,
  `CalendarWorkspaceReadResponse.people_source_available?` in
  `frontend/src/api/types.ts`.
- [x] 3.2 `LinkedPeopleAvatars` (pill avatar cluster) + `LinkedPeopleChips`
  (detail-panel chips) component; render on agenda pills and the event detail
  panel; degraded `SourceDegradedNote` when `people_source_available` is false.
- [x] 3.3 Vitest for the avatar cluster + chips hydration.

## 4. Spec + quality gates

- [x] 4.1 OpenSpec delta under
  `openspec/changes/calendar-workspace-linked-people/specs/dashboard-api/`.
- [ ] 4.2 `openspec validate calendar-workspace-linked-people --strict`.
- [x] 4.3 ruff check/format; targeted pytest (unit + integration).
- [x] 4.4 FE `npm run build` + `eslint .` + targeted vitest.
