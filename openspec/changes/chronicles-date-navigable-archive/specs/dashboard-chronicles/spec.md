# dashboard-chronicles MODIFIED

## MODIFIED Requirements

### Requirement: Chronicles Frontend Route

The dashboard SHALL expose a top-level route `/chronicles` rendered by
a page component at `frontend/src/pages/ChroniclesPage.tsx`. The route
SHALL render inside the standard dashboard shell (sidebar + header +
error boundary) and SHALL adopt the **editorial archetype** as defined
in `about/heart-and-soul/design-language.md` for its landing surface.
The landing SHALL be a date-navigable retrospective archive: the owner
SHALL be able to view any settled past day, not only the most recent one.

#### Scenario: Editorial archetype landing

- **WHEN** a user navigates to `/chronicles`
- **THEN** the dashboard shell SHALL render with the sidebar present
- **AND** the page content SHALL render the editorial archetype layout: a
  serif Voice column on the left (a date eyebrow carrying a prev/next day
  stepper, a stale-only briefing indicator, Display headline, Voice
  paragraph) and an index rail on the right (attention list, KPI strip,
  and a navigable recent-days index)
- **AND** the existing workspace components (Gantt, Map, Scrubber,
  Aggregations, Drawer) SHALL be reachable via a `<ChroniclesDrilldownPanel>`
  mounted below the editorial fold, disclosed on demand, and lazy-loaded on
  first interaction
- **AND** the route SHALL be wrapped in the dashboard's standard
  `ErrorBoundary`

#### Scenario: Operational timeline route preserved

- **WHEN** Chronicles is rendered
- **THEN** the existing `/timeline` route SHALL continue to render the
  unified operational stream of sessions, notifications, and errors
  unchanged
- **AND** Chronicles SHALL NOT mount any handler at `/timeline` or at
  any path under `/api/timeline`

### Requirement: Editorial Briefing Endpoint

The chronicler API SHALL expose `GET /api/chronicler/briefing?date=YYYY-MM-DD`
returning a `ChroniclesBriefing` object whose `voice_paragraph` is sourced
from the existing day-close Tier-2 cache or from a deterministic
templated fallback. The endpoint SHALL NOT initiate an LLM call. The
endpoint SHALL serve any historical date deterministically; when `date`
is omitted it SHALL default to the most recent settled day (yesterday in
the owner timezone).

#### Scenario: Response shape

- **WHEN** the endpoint returns successfully
- **THEN** the response body contains: `date`, `state_class` (one of
  `urgent`, `busy`, `mild`, `quiet`), `headline` (string), `voice_paragraph`
  (string), `voice_source` (one of `llm·cached`, `templated`, `stale`),
  `kpi` (object), `attention_items` (array), `recent_days` (array), and
  `earliest_date` (the earliest chronicled calendar day in the owner
  timezone as `YYYY-MM-DD`, or `null` when no episodes exist)
- **AND** every numeric field is `tabular-nums` safe (integer or fixed
  decimal)

#### Scenario: Day-close cache fresh

- **WHEN** `chronicler.tier2_cache` has a row with
  `cache_key = day_close:{date}` whose staleness check passes
- **THEN** `voice_paragraph` is the cached `prose`
- **AND** `voice_source` is `llm·cached`

#### Scenario: Day-close cache stale

- **WHEN** the cache row exists but the staleness check identifies an
  invalidating change
- **THEN** `voice_paragraph` is the cached prose
- **AND** `voice_source` is `stale`

#### Scenario: Day-close cache missing

- **WHEN** no cache row exists for the requested date
- **THEN** `voice_paragraph` is a deterministic templated string keyed by
  `state_class` and the KPI shape
- **AND** `voice_source` is `templated`

#### Scenario: State classification

- **WHEN** there is at least one attention item with severity `high`
- **THEN** `state_class` is `urgent`
- **WHEN** there are three or more attention items, none `high`
- **THEN** `state_class` is `busy`
- **WHEN** there are one or two attention items, none `high`
- **THEN** `state_class` is `mild`
- **WHEN** there are zero attention items
- **THEN** `state_class` is `quiet`

### Requirement: Editorial Attention Endpoint

The chronicler API SHALL expose `GET /api/chronicler/attention` returning
the list of attention items the briefing surfaces. The endpoint SHALL
accept the same `date` and `tz` parameters as the briefing and SHALL scope
its items to the requested day.

#### Scenario: Anomaly source: short sleep

- **WHEN** today's sleep_minutes is less than 0.7 × the median sleep_minutes
  of the prior seven days
- **THEN** an attention item with `kind = anomaly`, severity `medium`,
  and a title naming "Short sleep" is included

#### Scenario: Anomaly source: waking gap

- **WHEN** the contiguous gap between consecutive episodes within the
  window exceeds six hours during waking hours (06:00–22:00 in the
  owner's timezone)
- **THEN** one attention item per qualifying gap is included with
  `kind = anomaly`, severity `low`

#### Scenario: Source health degradation

- **WHEN** any chronicler source-state row carries a non-null `last_error`
  in the last twenty-four hours OR a non-null `inactive_reason`
- **AND** the requested date is the most recent settled day or today
- **THEN** an attention item with `kind = source_health` is included,
  severity `high` for `last_error`, severity `medium` for
  `inactive_reason`, and an `action_href` pointing to the connector page

#### Scenario: Source health excluded for older archive dates

- **WHEN** the requested date is older than the most recent settled day
- **THEN** no `source_health` attention item is included regardless of
  current connector state
- **AND** the day's `state_class` SHALL NOT be driven to `urgent` by
  present-day connector health

#### Scenario: Open corrections

- **WHEN** override rows exist whose target episode lies within the
  active window and whose `corrected_tombstone_at` is null
- **THEN** a single attention item with `kind = open_correction` is
  included carrying the count of unresolved overrides

### Requirement: Auto-Refresh Adoption

The Chronicles archive SHALL render only settled past days and SHALL NOT
auto-refresh. The page SHALL coin no new auto-refresh defaults and SHALL
provide a manual refresh control for the selected day's drilldown.

#### Scenario: Settled days are static

- **WHEN** the archive renders any selected day
- **THEN** the page SHALL NOT enable auto-refresh
- **AND** the page SHALL NOT mount a time-window picker or an
  auto-refresh toggle on this surface

#### Scenario: Manual refresh re-fetches the selected day

- **WHEN** the owner activates the manual refresh control
- **THEN** the drilldown queries for the selected day's window SHALL be
  invalidated and re-fetched

## ADDED Requirements

### Requirement: Archive Date Navigation

The Chronicles landing SHALL let the owner navigate between settled past
days. The selected day SHALL be URL state so a day view is deep-linkable,
and the selected day SHALL drive both the editorial briefing and the
drilldown. Navigation SHALL NOT initiate an LLM call; viewing any past
date reuses the existing cached or templated `voice_paragraph` path.

#### Scenario: Default landing day

- **WHEN** the owner opens `/chronicles` with no `date` query parameter
- **THEN** the page SHALL show the most recent settled day (yesterday in
  the owner timezone)

#### Scenario: Day selection is URL state

- **WHEN** the owner opens `/chronicles?date=YYYY-MM-DD` for a settled day
- **THEN** the briefing, attention, KPI, recent-days index, and drilldown
  SHALL all reconstruct that day
- **AND** changing the selected day SHALL update the `date` query parameter

#### Scenario: Stepper clamps to available range

- **WHEN** the owner steps forward
- **THEN** the page SHALL NOT advance past the most recent settled day
  (today is incomplete and not shown)
- **WHEN** the owner steps backward
- **THEN** the page SHALL NOT step before `earliest_date` from the
  briefing response

#### Scenario: Recent-days rows navigate

- **WHEN** the owner activates a row in the recent-days index
- **THEN** the page SHALL select that row's day and reconstruct it

#### Scenario: No new LLM call on navigation

- **WHEN** the owner navigates to any settled past day
- **THEN** the briefing handler SHALL NOT initiate an LLM call
- **AND** `voice_paragraph` SHALL come from the day-close Tier-2 cache
  (fresh or stale) or from the deterministic templated fallback
