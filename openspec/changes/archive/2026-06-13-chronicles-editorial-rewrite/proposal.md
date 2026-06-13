# Chronicles Editorial Rewrite

## Why

The `/chronicles` page is supposed to answer "what was my day". Today it opens
with a Gantt swimlane and aggregations, which read as a sysadmin-grade
operational stream rather than a retrospective briefing. The closed parent
epic `bu-zm1na` patched the projection pipeline (filtered butler-internal
sessions, fixed owntracks, registered spotify, added empty-lane affordance)
but the surface itself still sits in the workspace archetype: the user has
to scrub and chart-read to get the shape of their day.

Two distinct problems remain:

1. **Noise.** What survives the bu-zm1na filters still over-represents
   automated lanes (calendar, conversations) and under-represents the lived
   shape of the day.
2. **Gaps.** Three sources are missing entirely:
   - **Physical activity / health**: `google_health` adapter exists at
     `src/butlers/chronicler/adapters/google_health.py` but only projects
     sleep. Steps, heart rate, and workouts never reach Chronicler.
   - **Focus / deep-work blocks**: never captured. Long single-context
     work sessions are not surfaced as their own shape.
   - **Reading / learning sessions**: never captured.

Pipeline patches will not fix this because the surface itself is wrong:
the page shows facts before it speaks. The fix is to swap the page
archetype from workspace to **editorial** (per
`about/heart-and-soul/design-language.md`), give it a Voice briefing that
opens with a sentence, demote Gantt/Map/Drawer to drilldown, and project
the missing sources so the briefing has something to say.

## What Changes

- **Editorial archetype landing.** `/chronicles` MODIFIED to render the
  editorial archetype: serif Voice briefing on the left, KPI strip and
  attention list on the right, recent-days index below. Existing
  `GanttSwimlane`, `FloatingMapMinimap`, `Scrubber`, `AggregateStackedBar`,
  `AggregatePieChart`, `EpisodeDrawer`, `SourceStateBadgeStrip`,
  `StreakCallouts` MOVED into a `<ChroniclesDrilldownPanel>` mounted
  on demand below the editorial fold.
- **New API endpoints (additive).**
  - `GET /api/chronicler/briefing?date=YYYY-MM-DD` returning a
    `ChroniclesBriefing` object.
  - `GET /api/chronicler/attention?since=ISO&until=ISO` returning the
    attention list.
  - `GET /api/chronicler/kpi?date=YYYY-MM-DD` returning the KPI snapshot.
  All read from `chronicler.*` only. None invokes a new LLM call;
  `voice_paragraph` reads the existing day-close cache (Tier-2) and falls
  back to a templated string when the cache is missing or stale.
- **Health adapter promoted.** `google_health.py` extended to project
  workouts (`workout_episode`, category `other`), steps (point events,
  `health.steps`), and heart rate (point events, `health.heart_rate`).
  Sleep projection is unchanged.
- **New focus adapter.** `chronicler/adapters/focus.py` projects
  `focus_block` episodes (category `tasks`) inferred from long single-context
  `core.sessions` and calendar events titled with focus keywords.
- **New reading adapter.** `chronicler/adapters/reading.py` projects
  `reading_block` episodes (category `tasks`) inferred from calendar events
  titled with reading keywords (and, optionally, `health.facts` rows with
  predicate `reading_session` when present).
- **Lane taxonomy preserved.** No new lanes; new episode types fold into
  existing categories (`sleep`, `tasks`, `other`).
- **No manifesto change.** Chronicler remains retrospective, deterministic
  per-event, Tier-2 LLM only on day-close / drilldown / correction-assist.
- **Existing endpoints preserved.** `/episodes`, `/events`, `/aggregate/*`,
  `/source-state`, `/projection-health`, `/ops/sessions`,
  `/episodes/{id}/explain` continue to back the drilldown panel.

## Capabilities

### Modified Capabilities

- `dashboard-chronicles`: page archetype changes from workspace to editorial
  for the landing surface. Drilldown surfaces (Gantt / Map / aggregations
  / drawer) preserved as on-demand. New API endpoints for briefing /
  attention / KPI added; existing endpoints unchanged. New episode types
  (`workout_episode`, `focus_block`, `reading_block`) and new point-event
  sources (`health.steps`, `health.heart_rate`) added without lane
  taxonomy reshape.

## Impact

- **New backend modules:** `src/butlers/chronicler/adapters/focus.py`,
  `src/butlers/chronicler/adapters/reading.py`. Extension to
  `src/butlers/chronicler/adapters/google_health.py`. Aggregations map
  updated in `src/butlers/chronicler/aggregations.py`. Job registration
  in `src/butlers/chronicler/jobs.py`.
- **New API surface:** three additive endpoints in
  `roster/chronicler/api/router.py` plus matching Pydantic models in
  `roster/chronicler/api/models.py`. No deletions in this change.
- **Frontend rewrite:** `frontend/src/pages/ChroniclesPage.tsx` rewritten
  as editorial-archetype consumer; new hooks (`use-chronicles-briefing`,
  `use-chronicles-attention`, `use-chronicles-kpi`); new components
  `RecentDaysIndex` and `ChroniclesDrilldownPanel` under
  `frontend/src/components/chronicles/`. Existing 25 chronicles components
  preserved, mounted via the drilldown panel.
- **No database schema changes.** Existing `chronicler.episodes` and
  `chronicler.point_events` tables accept the new episode types without
  migration. New source names register through normal projection flow.
- **No LLM call paths added.** Briefing reads existing day-close
  Tier-2 cache; refresh remains a separate, rate-limited explicit user
  action via the existing `/aggregate/day-close/refresh` endpoint.
- **Specs touched:** `dashboard-chronicles` MODIFIED.
