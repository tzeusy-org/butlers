# Dashboard Chronicles

## Purpose

Defines the Chronicles page surface contract for the Butlers dashboard:
a single retrospective view that overlays concurrent activity lanes
(work, calendar, music, gaming, travel, sleep, meal, home) on a shared
time scrubber, paired with a map widget for location replay and an
aggregations panel for time-by-category breakdowns. The page consumes
Chronicler-owned data exclusively (`/api/chronicler/*`) and is
distinct from the operational `/timeline` route, which remains the
live cross-butler ops stream.

## ADDED Requirements

### Requirement: Chronicles Frontend Route

The dashboard SHALL expose a top-level route `/chronicles` rendered by
a page component at `frontend/src/pages/ChroniclesPage.tsx`. The route
SHALL render inside the standard dashboard shell (sidebar + header +
error boundary).

#### Scenario: Route renders inside shell

- **WHEN** a user navigates to `/chronicles`
- **THEN** the dashboard shell SHALL render with the sidebar present
- **AND** the page content area SHALL render the Chronicles widgets
  (time-window picker, source-state badge strip, Gantt, map,
  aggregations panel)
- **AND** the route SHALL be wrapped in the dashboard's standard
  `ErrorBoundary` so that widget failures do not crash the shell

#### Scenario: Operational timeline route preserved

- **WHEN** Chronicles is added
- **THEN** the existing `/timeline` route SHALL continue to render the
  unified operational stream of sessions, notifications, and errors
  unchanged
- **AND** Chronicles SHALL NOT mount any handler at `/timeline` or at
  any path under `/api/timeline`

### Requirement: Sidebar Placement and Discrimination

The sidebar nav config in `frontend/src/components/layout/nav-config.ts` SHALL place the Chronicles entry under the Dedicated Butlers section, not under Telemetry. Tooltips SHALL distinguish Chronicles from the operational timeline view.

#### Scenario: Dedicated Butlers section placement

- **WHEN** the sidebar is rendered
- **THEN** the Chronicles entry SHALL appear under the Dedicated
  Butlers section
- **AND** Chronicles SHALL NOT be placed under the Telemetry section
  alongside Timeline / Notifications / Issues / Audit Log

#### Scenario: Tooltip discrimination from /timeline

- **WHEN** a user hovers the `/chronicles` sidebar entry
- **THEN** the tooltip SHALL read "Retrospective lived-time
  reconstruction"
- **AND** the `/timeline` sidebar tooltip SHALL state that it is the
  live cross-butler operational stream

### Requirement: Page-Level Invariants

Chronicles page handlers (frontend hooks, render components) AND backend aggregate / source-state handlers SHALL NOT introduce LLM invocations or cross-schema reads beyond what Chronicler already owns.

#### Scenario: No new LLM call paths

- **WHEN** the page is rendered, scrolled, scrubbed, or auto-refreshed
- **THEN** no LLM call SHALL be initiated by page-driven code
- **AND** the only LLM-bearing user action permitted SHALL be an
  explicit click that re-invokes the existing scheduled
  `chronicler_day_close` Tier-2 entry point (RFC 0014 §D5),
  rate-limited to 1 per day per window

#### Scenario: No cross-schema reads

- **WHEN** any backend handler that the page consumes executes SQL
- **THEN** the SQL SHALL reference only `chronicler.*` schema
  relations
- **AND** a guardrail test SHALL fail any change that introduces a
  non-`chronicler.` schema reference in a Chronicles-page handler

### Requirement: Category Taxonomy Mapping

A pure deterministic backend function SHALL map every
`(source_name, episode_type)` pair to a stable category string. The
frontend SHALL own the visual presentation (colour, label, icon)
keyed off the returned category.

#### Scenario: Deterministic mapping function

- **WHEN** the backend computes the `category` field for an episode
  or aggregate bucket record
- **THEN** the result SHALL be one of `work`, `calendar`, `music`,
  `gaming`, `travel`, `sleep`, `meal`, `home`, or `other`
- **AND** the mapping function SHALL be pure (no I/O, no LLM)
- **AND** a unit test SHALL assert that every `(source_name,
  episode_type)` projected by an active adapter maps to a non-`other`
  category

#### Scenario: Frontend lane taxonomy is source of truth for visuals

- **WHEN** the frontend renders a lane
- **THEN** the lane label, colour, icon, and sort order SHALL be
  resolved from `frontend/src/components/chronicles/lane-taxonomy.ts`
  using the backend-supplied `category` as the key
- **AND** the backend SHALL NOT return colour or icon strings

### Requirement: Disabled Lane Affordances

The page SHALL render lane controls for every category in the taxonomy,
adjusting state based on `/api/chronicler/source-state` so that the
operator can see which categories are unblocked, unavailable, or
explicitly deferred.

#### Scenario: Supported and active source

- **WHEN** a source's `chronicler_compatibility = supported` AND
  `active = true`
- **THEN** the corresponding lane SHALL be rendered enabled with no
  banner

#### Scenario: Supported but inactive

- **WHEN** a source's `chronicler_compatibility = supported` AND
  `active = false`
- **THEN** the lane SHALL render with a yellow "no recent data" banner
- **AND** the banner tooltip SHALL show the source's
  `inactive_reason` and the latest `last_error`

#### Scenario: Planned source

- **WHEN** a source's `chronicler_compatibility = planned`
- **THEN** the lane SHALL render disabled with the tooltip "Adapter
  planned; not yet implemented"

#### Scenario: Deferred source

- **WHEN** a source's `chronicler_compatibility = deferred`
- **THEN** the lane SHALL be hidden by default
- **AND** the page SHALL provide a toggle to reveal deferred lanes for
  diagnostic purposes

#### Scenario: Not-time-bearing source

- **WHEN** a source's `chronicler_compatibility = not_time_bearing`
- **THEN** the source SHALL never be rendered as a lane

### Requirement: Map Render Privacy Contract

The map widget SHALL enforce privacy and tombstone rules at render
time. Default API parameters SHALL produce a privacy-safe view; the
frontend SHALL NOT relax defaults without an explicit user-toggle
gated by a future settings surface.

#### Scenario: Restricted episodes excluded entirely

- **WHEN** an episode or point event has `privacy_tier = restricted`
- **THEN** the page SHALL NOT render it on the Gantt or the map
- **AND** the underlying API request SHALL omit `restricted` from the
  `privacy_tier` query parameter unless explicitly overridden
- **AND** this default-exclusion of `restricted` SHALL apply to the
  page's calls to existing `Chronicler Temporal Reads` endpoints
  (`/api/chronicler/episodes`, `/api/chronicler/events`) as well as the
  new aggregate endpoints, even though the upstream `Chronicler Temporal
  Reads` Requirement does not impose this default at the API layer

#### Scenario: Sensitive episodes masked

- **WHEN** an episode has `privacy_tier = sensitive`
- **THEN** the Gantt SHALL render the lane bar as a generic masked
  entry (no title, no payload contents)
- **AND** the map SHALL NOT plot any coordinates derived from that
  episode or its linked point events

#### Scenario: Tombstoned data excluded by default

- **WHEN** the page issues an aggregate, episode, or point-event
  request
- **THEN** it SHALL omit `include_tombstoned` (default `false`) so
  that tombstoned rows are excluded
- **AND** any future operator-visible "show tombstoned" toggle SHALL
  surface a clear visual indicator that tombstoned data is rendered

#### Scenario: Retention enforcement is upstream

- **WHEN** retention windows expire on a source (e.g. OwnTracks
  default 30-day retention per `security.md` L172–175)
- **THEN** the projection adapter and storage layer SHALL drop expired
  rows
- **AND** the map widget SHALL NOT add a separate retention filter

### Requirement: Day-Close Cache Invalidation

Cached `chronicler_day_close` Tier-2 prose SHALL be invalidated and
visually flagged stale whenever any episode, point event, or override
in the cached window changes after the cache was built.

#### Scenario: Cache stale on tombstone

- **WHEN** any row in `chronicler.episodes` or
  `chronicler.point_events` within the cached window has
  `tombstone_at > cache_built_at`
- **THEN** the cache entry SHALL be reported stale
- **AND** the page SHALL render the "Summary out of date — last
  refreshed YYYY-MM-DD" affordance instead of the cached prose

#### Scenario: Cache stale on update

- **WHEN** any row in `chronicler.episodes` or
  `chronicler.point_events` within the cached window has `updated_at
  > cache_built_at`
- **THEN** the cache entry SHALL be reported stale

#### Scenario: Cache stale on override

- **WHEN** any row in `chronicler.overrides` whose target falls in the
  cached window has `created_at > cache_built_at`
- **THEN** the cache entry SHALL be reported stale
- **AND** this rule SHALL apply even if `episodes.updated_at` is
  unchanged (because precision-reduction or correction landed on the
  override row)

#### Scenario: User-clicked refresh re-invokes existing path

- **WHEN** the user clicks the "regenerate" affordance on a stale
  cache entry
- **THEN** the page SHALL POST to a re-invocation endpoint that re-runs
  the existing scheduled `chronicler_day_close` Tier-2 entry point
- **AND** the re-invocation SHALL be rate-limited to 1 per day per
  window
- **AND** no new LLM call path SHALL be introduced

### Requirement: Auto-Refresh Adoption

The page SHALL adopt the existing `useAutoRefresh` hook for its
"today" window and SHALL coin no new auto-refresh defaults.

#### Scenario: Today window polling

- **WHEN** the time-window picker selects a window whose `end_at` is
  within the current day
- **THEN** the page SHALL enable auto-refresh at 30 seconds by default
- **AND** the user SHALL be able to override to 10 / 30 / 60 seconds
  via the existing `AutoRefreshToggle` component, or pause refresh

#### Scenario: Older windows are static

- **WHEN** the time-window picker selects a window whose `end_at` is
  before the current day
- **THEN** the page SHALL NOT enable auto-refresh
- **AND** the page SHALL provide a manual refresh button

### Requirement: MapLibre Dependency Justification

The page SHALL use `maplibre-gl` (BSD-3 license) for the map widget,
with OpenStreetMap as the tile source. Dependency rationale SHALL be
documented in this spec and in the change's design.md.

#### Scenario: License and tile source

- **WHEN** `maplibre-gl` is added to `frontend/package.json`
- **THEN** the dependency SHALL be the BSD-3-licensed open-source
  fork
- **AND** the tile source SHALL be OpenStreetMap (no API token, no
  third-party hosted-tile commercial dependency)

#### Scenario: Bundle measurement

- **WHEN** the dependency is added
- **THEN** a measurement SHALL be recorded comparing pre-merge and
  post-merge frontend bundle sizes per
  `craft-and-care/performance-discipline.md` measure-before-optimize
- **AND** any regression SHALL be discussed in the PR description

### Requirement: Page Telemetry

Backend handlers serving the page SHALL emit OTel spans for the new endpoints so that operational health is observable without log scraping. Client-side telemetry is out of scope for this change.

#### Scenario: Backend spans

- **WHEN** an aggregate or source-state endpoint executes
- **THEN** an OTel span SHALL be emitted with the name pattern
  `chronicler.aggregate.by_category`,
  `chronicler.aggregate.by_day`,
  `chronicler.aggregate.day_close`, or `chronicler.source_state`
- **AND** the span SHALL record query latency and the resulting bucket
  count (or, for source-state and day-close, the row / cache state)
