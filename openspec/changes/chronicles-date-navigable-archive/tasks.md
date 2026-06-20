# Tasks

## 1. Spec landing

- [x] 1.1 Create `openspec/changes/chronicles-date-navigable-archive/{proposal,design,tasks}.md`.
- [x] 1.2 Add the `dashboard-chronicles` delta under `specs/dashboard-chronicles/spec.md` (MODIFIED: Chronicles Frontend Route, Editorial Briefing Endpoint, Editorial Attention Endpoint, Auto-Refresh Adoption; ADDED: Archive Date Navigation).
- [x] 1.3 Validate via `openspec validate chronicles-date-navigable-archive --strict`.

## 2. Backend: date-scoped source health + earliest_date (TDD)

- [x] 2.1 Add failing tests in `tests/chronicler/test_editorial.py`: `_target_is_recent` rule; `_utc_to_local_date` conversion; `compose_briefing_payload` excludes `source_health` for an old date and includes it for yesterday (injectable `now`); `compose_briefing_payload` surfaces `earliest_date`.
- [x] 2.2 `editorial.py`: add `_target_is_recent(target, tz_name, now)`, `_utc_to_local_date(dt, tz_name)`, `_fetch_earliest_episode_date(pool, tz_name)`; thread injectable `now` through `_fetch_source_health_items` and `compose_briefing_payload`; gate source-health inclusion; add `earliest_date` to `BriefingPayload`.
- [x] 2.3 `roster/chronicler/api/models.py`: add `ChroniclesBriefing.earliest_date: str | None = None`.
- [x] 2.4 `roster/chronicler/api/router.py`: map `payload.earliest_date` into the briefing response.
- [x] 2.5 Run `uv run pytest tests/chronicler/test_editorial.py tests/chronicler/test_editorial_api.py -q`.

## 3. Frontend: date navigation (TDD)

- [x] 3.1 `frontend/src/api/types.ts`: add `earliest_date?: string | null` to `ChroniclesBriefing`.
- [x] 3.2 Add `frontend/src/pages/chronicles-date-nav.ts` pure helpers (`nextIsoDay`, `prevIsoDay`, `clampIsoDay`, `isAtLatest`, `isAtEarliest`, `greetSubject`) + unit tests `chronicles-date-nav.test.ts`.
- [x] 3.3 `RecentDaysIndex.tsx`: add optional `onSelect(date)` prop; render rows as buttons when provided; mark the active day. Add `RecentDaysIndex.test.tsx` (SSR row markup + `react-dom/client` click → onSelect).
- [x] 3.4 `ChroniclesPage.tsx`: URL `?date=` state via `useSearchParams` (default = yesterday, clamped to `[earliest_date, yesterday]`); prev/next stepper in the eyebrow; date-relative greet; demoted stale-only pill; attention list above KPI strip; KPI top-lane numeric fix; pass the selected day to the drilldown.
- [x] 3.5 `ChroniclesDrilldownPanel.tsx`: accept the selected-day window as a prop; drop `useTimeWindow`/`TimeWindowPicker`/`useAutoRefresh`/`AutoRefreshToggle`; rename `Gantt area`/`Aggregations area` to owner language and drop the card chrome; wrap the body in a disclosure that mounts on expand; remove the `pb-72` void.
- [x] 3.6 Update `ChroniclesPage.test.tsx`: default still requests yesterday; `?date=` deep-link drives the briefing date; stepper interaction (`react-dom/client`) changes the requested date and clamps at yesterday; pill assertions updated to stale-only.

## 4. Integration and verification

- [x] 4.1 `uv run ruff check src/ tests/ roster/ conftest.py --output-format concise`.
- [x] 4.2 `uv run ruff format --check src/ tests/ roster/ conftest.py -q`.
- [x] 4.3 `uv run pytest tests/chronicler -q --maxfail=3 --tb=short`.
- [x] 4.4 `cd frontend && npm run build` (full `tsc -b` + vite), `npx eslint .`, `npx vitest run` (chronicles + page scope).

## 5. Out of scope

- Multi-day / custom-range analytics in the Chronicles drilldown.
- Pop-over calendar month picker (no calendar primitive exists yet).
- Live "today so far" mode.
- Refreshing the stale `archetype="workspace"` Chronicles reference in
  `about/lay-and-land/frontend.md` (pre-existing drift, tracked in bu-26j38).
