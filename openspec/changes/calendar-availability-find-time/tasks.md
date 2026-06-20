## 1. Owner scheduling-availability preferences (foundation, bu-vj0ax8)

- [ ] 1.1 Add owner-scoped scheduling-preferences storage (`earliest_meeting_time`, `latest_meeting_time`, `meeting_days`, `timezone`, `no_meeting_blocks`), distinct from per-butler notification `delivery_preferences`
- [ ] 1.2 Add `scheduling_preferences_get` / `scheduling_preferences_set` MCP tools (reject invalid timezone; no row → no constraints)
- [ ] 1.3 Thread the loaded scheduling preferences into `_build_suggested_slots` so it never proposes slots outside the allowed hours/days or inside a `no_meeting_blocks` interval
- [ ] 1.4 Unit tests: set/get round-trip; `_build_suggested_slots` with prefs clips out-of-hours/weekend slots; with no prefs row, behaves as today

## 2. Generalize free/busy into a windowed multi-calendar provider method (bu-140q93)

- [ ] 2.1 Add `get_free_busy(calendar_ids, start_at, end_at, timezone=None)` to the `CalendarProvider` ABC returning merged busy windows
- [ ] 2.2 Implement `get_free_busy` on `_GoogleProvider` by reusing the existing `/freeBusy` request body and `calendars`/`busy[]` parsing (multi-calendar `items`, arbitrary window); no new OAuth scope
- [ ] 2.3 Refactor `find_conflicts` to delegate to `get_free_busy` for the single-calendar candidate window, preserving its existing signature and return shape
- [ ] 2.4 Unit tests: `get_free_busy` merges windows across multiple calendar ids and returns empty on no busy; post-refactor `find_conflicts` matches the prior single-calendar output (golden regression)

## 3. `calendar_find_free_slots` MCP tool

- [ ] 3.1 Add `calendar_find_free_slots(duration_minutes, search_start, search_end, calendar_ids=None, constraints=None, limit=...)` that queries `get_free_busy`, subtracts busy windows, clips to owner scheduling prefs, splits gaps into duration-sized slots, ranks earliest-first with constraint matches preferred
- [ ] 3.2 Update the `module-calendar` "Calendar Event CRUD Tools" registered-tool count literal from "16 MCP tools total" to "17 MCP tools total"
- [ ] 3.3 Unit tests: subtract busy windows respecting duration; respect owner prefs (no 6am, no Sunday, skip lunch block); empty result when window fully busy; `limit` honored

## 4. `POST /api/calendar/workspace/find-time` endpoint

- [ ] 4.1 Add request/response Pydantic models to `src/butlers/api/models/calendar_workspace.py`
- [ ] 4.2 Add `POST /api/calendar/workspace/find-time` to `calendar_workspace.py` calling `calendar_find_free_slots` via the MCP bridge; return ranked slots
- [ ] 4.3 API tests: ranked slots returned for a valid request; validation error on bad duration/window

## 5. Spec + regression + validate

- [ ] 5.1 Update `connector-google-calendar` to document that the connector's already-granted `calendar` scope authorizes free/busy queries (no scope change)
- [ ] 5.2 Update/replace regression tests touched by the `find_conflicts` refactor
- [ ] 5.3 Run `openspec validate calendar-availability-find-time --strict`
- [ ] 5.4 Quality gate: `ruff check`/`format --check` + targeted calendar + time-aware-delivery suites, then full `pytest` (excluding e2e) before merge
