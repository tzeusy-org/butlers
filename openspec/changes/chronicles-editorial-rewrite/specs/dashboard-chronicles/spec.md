# dashboard-chronicles MODIFIED

## MODIFIED Requirements

### Requirement: Chronicles Frontend Route

The dashboard SHALL expose a top-level route `/chronicles` rendered by
a page component at `frontend/src/pages/ChroniclesPage.tsx`. The route
SHALL render inside the standard dashboard shell (sidebar + header +
error boundary) and SHALL adopt the **editorial archetype** as defined
in `about/heart-and-soul/design-language.md` for its landing surface.

#### Scenario: Editorial archetype landing

- **WHEN** a user navigates to `/chronicles`
- **THEN** the dashboard shell SHALL render with the sidebar present
- **AND** the page content SHALL render the editorial archetype layout: a
  serif Voice column on the left (DateEyebrow, status pill, Display
  headline, Voice paragraph) and an index rail on the right (KPI strip,
  attention list, and recent-days index)
- **AND** the existing workspace components (Gantt, Map, Scrubber,
  Aggregations, Drawer) SHALL be reachable via a `<ChroniclesDrilldownPanel>`
  mounted below the editorial fold and lazy-loaded on first interaction
- **AND** the route SHALL be wrapped in the dashboard's standard
  `ErrorBoundary`

#### Scenario: Operational timeline route preserved

- **WHEN** Chronicles is rendered
- **THEN** the existing `/timeline` route SHALL continue to render the
  unified operational stream of sessions, notifications, and errors
  unchanged
- **AND** Chronicles SHALL NOT mount any handler at `/timeline` or at
  any path under `/api/timeline`

### Requirement: Page-Level Invariants

The Chronicles page SHALL keep frontend hooks, render components, and
backend briefing / attention / KPI handlers inside Chronicler's existing
ownership boundaries. These handlers SHALL NOT introduce LLM invocations
or cross-schema reads beyond what Chronicler already owns.

#### Scenario: No new LLM call paths

- **WHEN** the briefing, attention, KPI, or any page-driven endpoint is
  invoked
- **THEN** no LLM call SHALL be initiated by the request handler
- **AND** the only LLM-bearing user action permitted SHALL be an explicit
  click that re-invokes the existing scheduled `chronicler_day_close`
  Tier-2 entry point via `POST /api/chronicler/aggregate/day-close/refresh`
- **AND** the briefing endpoint SHALL source its `voice_paragraph` from
  `chronicler.tier2_cache` (fresh or stale) or from a deterministic
  templated fallback; it SHALL NOT call the LLM directly

#### Scenario: No cross-schema reads

- **WHEN** any backend handler that the page consumes executes SQL
- **THEN** the SQL SHALL reference only `chronicler.*` schema relations
- **AND** a guardrail test SHALL fail any change that introduces a
  non-`chronicler.` schema reference in a Chronicles-page handler

### Requirement: Category Taxonomy Mapping

A pure deterministic backend function SHALL map every
`(source_name, episode_type)` pair to a stable category string. The
frontend SHALL own the visual presentation (colour, label, icon) keyed
off the returned category. The taxonomy SHALL retain its existing ten
categories: `conversations`, `tasks`, `calendar`, `music`, `gaming`,
`travel`, `sleep`, `meal`, `home`, `other`.

#### Scenario: New episode types fold into existing categories

- **WHEN** the backend computes the `category` field for an episode
- **AND** the source / episode_type pair is one of the new types
- **THEN** `(google_health.measurements, workout_episode) → other`
- **AND** `(chronicler.focus_inferred, focus_block) → tasks`
- **AND** `(chronicler.reading_inferred, reading_block) → tasks`
- **AND** no new category string SHALL be introduced

## ADDED Requirements

### Requirement: Editorial Briefing Endpoint

The chronicler API SHALL expose `GET /api/chronicler/briefing?date=YYYY-MM-DD`
returning a `ChroniclesBriefing` object whose `voice_paragraph` is sourced
from the existing day-close Tier-2 cache or from a deterministic
templated fallback. The endpoint SHALL NOT initiate an LLM call.

#### Scenario: Response shape

- **WHEN** the endpoint returns successfully
- **THEN** the response body contains: `date`, `state_class` (one of
  `urgent`, `busy`, `mild`, `quiet`), `headline` (string), `voice_paragraph`
  (string), `voice_source` (one of `llm·cached`, `templated`, `stale`),
  `kpi` (object), `attention_items` (array), `recent_days` (array)
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
the list of attention items the briefing surfaces.

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
- **THEN** an attention item with `kind = source_health` is included,
  severity `high` for `last_error`, severity `medium` for
  `inactive_reason`, and an `action_href` pointing to the connector page

#### Scenario: Open corrections

- **WHEN** override rows exist whose target episode lies within the
  active window and whose `corrected_tombstone_at` is null
- **THEN** a single attention item with `kind = open_correction` is
  included carrying the count of unresolved overrides

### Requirement: Editorial KPI Endpoint

The chronicler API SHALL expose `GET /api/chronicler/kpi?date=YYYY-MM-DD`
returning the KPI snapshot the briefing also embeds.

#### Scenario: KPI fields

- **WHEN** the endpoint returns successfully
- **THEN** the response includes `hours_by_top_lanes` (top three by
  total minutes), `longest_episode_minutes`, `longest_episode_title`,
  `longest_gap_minutes`, `sleep_minutes`, and `streaks` (a small object
  with `sleep` and `exercise` integer streak counts)
