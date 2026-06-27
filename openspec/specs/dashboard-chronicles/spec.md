# dashboard-chronicles Specification

## Purpose

Defines the dashboard surface contract for the Chronicles page — the retrospective
lived-time reconstruction UI backed by Chronicler-owned API endpoints. The page
renders at `/chronicles` inside the standard dashboard shell and is distinct from
the operational `/timeline` route (live cross-butler ops stream). This spec was
promoted from the `add-dashboard-chronicles` OpenSpec change upon archive.
## Requirements
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
- **THEN** the SQL SHALL reference only `chronicler.*` schema
  relations
- **AND** a guardrail test SHALL fail any change that introduces a
  non-`chronicler.` schema reference in a Chronicles-page handler

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

### Requirement: Editorial KPI Endpoint

The chronicler API SHALL expose `GET /api/chronicler/kpi?date=YYYY-MM-DD`
returning the KPI snapshot the briefing also embeds.

#### Scenario: KPI fields

- **WHEN** the endpoint returns successfully
- **THEN** the response includes `hours_by_top_lanes` (top three by
  total minutes), `longest_episode_minutes`, `longest_episode_title`,
  `longest_gap_minutes`, `sleep_minutes`, and `streaks` (a small object
  with `sleep` and `exercise` integer streak counts)

### Requirement: Category Taxonomy Mapping

A pure deterministic backend function SHALL map every
`(source_name, episode_type)` pair to a stable category string. The
frontend SHALL own the visual presentation (colour, label, icon) keyed
off the returned category. The taxonomy SHALL retain its existing ten
categories: `conversations`, `tasks`, `calendar`, `music`, `gaming`,
`travel`, `sleep`, `meal`, `home`, `other`.

#### Scenario: Deterministic mapping function

- **WHEN** the backend computes the `category` field for an episode
  or aggregate bucket record
- **THEN** the result SHALL be one of `conversations`, `tasks`,
  `calendar`, `music`, `gaming`, `travel`, `sleep`, `meal`, `home`, or
  `other`
- **AND** the mapping function SHALL be pure (no I/O, no LLM)
- **AND** a unit test SHALL assert that every `(source_name,
  episode_type)` projected by an active adapter maps to a non-`other`
  category

#### Scenario: New episode types fold into existing categories

- **WHEN** the backend computes the `category` field for an episode
- **AND** the source / episode_type pair is one of the new types
- **THEN** `(google_health.measurements, workout_episode) → other`
- **AND** `(chronicler.focus_inferred, focus_block) → tasks`
- **AND** `(chronicler.reading_inferred, reading_block) → tasks`
- **AND** no new category string SHALL be introduced

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

The map widget and Gantt swimlane SHALL enforce privacy and tombstone
rules at render time. Default API parameters SHALL produce a
privacy-safe view; the frontend SHALL NOT relax defaults without an
explicit user-toggle gated by the `Per-Recipient Masking Toggle`
requirement.

The classification of a row as `sensitive` is a source-level decision
made by the projection adapter — it does NOT imply that the dashboard
viewer is untrusted. Per the owner-view doctrine in
`about/heart-and-soul/security.md` L168–185, the Butlers instance has
a single trusted viewer (the owner) and "the system does not apply
differential privacy, anonymization, or special-purpose encryption to
any data category." Adapters SHOULD therefore default to
`privacy=normal` for owner-originated data; the `sensitive` tier exists
for rows whose payload masks make sense for shared, screenshot, or
third-party views once the per-recipient toggle is implemented.

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
- **AND** the dashboard is rendering for the owner with no per-recipient
  masking toggle engaged
- **THEN** the Gantt SHALL render the lane bar as a generic masked
  entry (no title, no payload contents)
- **AND** the map SHALL NOT plot any coordinates derived from that
  episode or its linked point events
- **AND** the spec MAKES NO CLAIM about which adapters emit `sensitive`
  rows by default — that decision lives with each projection adapter
  per the owner-view doctrine. As of `core_086`, no in-tree adapter
  defaults to `sensitive`; rows reach this tier only via per-row
  corrections or future adapter changes.

#### Scenario: Tombstoned data excluded by default

- **WHEN** the page issues an aggregate, episode, or point-event
  request
- **THEN** it SHALL omit `include_tombstoned` (default `false`) so
  that tombstoned rows are excluded
- **AND** any future operator-visible "show tombstoned" toggle SHALL
  surface a clear visual indicator that tombstoned data is rendered

#### Scenario: Retention enforcement is upstream

- **WHEN** retention windows expire on a source (e.g. OwnTracks
  default 30-day retention per `about/heart-and-soul/security.md`
  L172–175)
- **THEN** the projection adapter and storage layer SHALL drop expired
  rows
- **AND** the map widget SHALL NOT add a separate retention filter

### Requirement: Per-Recipient Masking Toggle

The Chronicles dashboard SHALL gate the relaxation of `sensitive`-tier
masking on an explicit viewer-context signal. The default rendering
posture SHALL be fail-safe-closed: in the absence of a viewer-context
that identifies the viewer as the owner, all `sensitive`-tier episodes
and their derived map coordinates SHALL be rendered as masked
envelopes per the `Sensitive episodes masked` scenario.

This requirement is forward-looking. The current dashboard runs only
for the owner behind session-cookie auth, so today the viewer is
unconditionally the owner and `sensitive` masking is effectively
inactive (because no in-tree adapter emits `sensitive` rows by
default). The requirement codifies the contract the dashboard MUST
satisfy *if* shared-link, screenshot-publish, or third-party viewer
flows are added in the future.

The shape of the viewer-context plumbing (session role enum, share-link
tokens, screenshot-mode flag) is OUT OF SCOPE for this requirement —
those decisions belong to the implementing change.

#### Scenario: Owner viewer renders sensitive rows fully

- **WHEN** the dashboard renders for a viewer whose viewer-context
  identifies them as the owner
- **AND** an episode has `privacy_tier = sensitive`
- **THEN** the Gantt bar and map coordinates for that episode SHALL
  render with full title and payload, exactly as if the episode were
  `privacy_tier = normal`
- **AND** no per-row toggle SHALL be required for the owner to see
  their own data

#### Scenario: Non-owner viewer triggers fail-safe masking

- **WHEN** the dashboard renders for a viewer whose viewer-context
  does NOT identify them as the owner (e.g. a share-link viewer, a
  screenshot-publish render, a future third-party viewer)
- **AND** an episode has `privacy_tier = sensitive`
- **THEN** the Gantt SHALL render the lane bar as a generic masked
  entry (no title, no payload contents) per the `Sensitive episodes
  masked` scenario
- **AND** the map SHALL NOT plot any coordinates derived from that
  episode or its linked point events
- **AND** there SHALL be no frontend escape hatch — relaxation of the
  mask SHALL require an explicit owner-side configuration change, not
  a viewer-side toggle

#### Scenario: Absent viewer-context is treated as non-owner

- **WHEN** the dashboard renders without a resolvable viewer-context
  (e.g. unauthenticated request, missing session, malformed token)
- **THEN** the page SHALL apply the non-owner masking posture
  (fail-safe-closed)
- **AND** the page MAY additionally redirect to authentication, but
  SHALL NOT render unmasked `sensitive` content while the
  viewer-context is unresolved

#### Scenario: Toggle state is observable for audit

- **WHEN** the dashboard renders for any viewer
- **THEN** the rendered page SHALL expose the resolved viewer-context
  classification (owner / non-owner / unresolved) in a way an
  end-to-end test or audit log can read (e.g. a `data-viewer-role`
  attribute on a stable container element)
- **AND** the audit signal SHALL NOT itself be a vector for relaxing
  the mask — reading the role does not change it

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

### Requirement: Map Widget Style-Load Resilience

The map widget SHALL defer source and layer mutations until the
underlying MapLibre tile style has finished loading. Calling
`map.addSource(...)` or `map.addLayer(...)` synchronously after
`new maplibreGl.Map(...)` throws `Style is not done loading` because
the style fetch is asynchronous; that exception bubbles into
`MapErrorBoundary` and renders the user-visible `Failed to load the
map. Try again` fallback even when valid trail or point data exists.

#### Scenario: Trail-only first mount succeeds

- **WHEN** the Chronicles page mounts the map widget for the first
  time with `points = []` and `trailPoints` containing two or more
  coordinate pairs
- **THEN** the map canvas SHALL render the OSM tile layer plus the
  trail line layer
- **AND** the widget SHALL NOT fall through to the
  `MapErrorBoundary` fallback

#### Scenario: Trail data updates after style is loaded use setData

- **WHEN** the map style has already loaded AND `trailPoints` updates
- **THEN** the existing trail GeoJSON source SHALL be updated via
  `setData(...)` rather than re-added
- **AND** no re-mount of the map instance SHALL occur for trail-only
  changes

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

## Source References

- `about/heart-and-soul/design-language.md` — editorial archetype
  definition for the Chronicles landing surface.
- `about/heart-and-soul/security.md` L168–185 — owner-view doctrine
  (single trusted viewer; no differential privacy/anonymization).
- `about/heart-and-soul/security.md` L172–175 — OwnTracks default
  30-day retention enforced upstream of the map widget.
- RFC 0014 §D5 — scheduled `chronicler_day_close` Tier-2 entry point.
- `craft-and-care/performance-discipline.md` — measure-before-optimize
  discipline for the `maplibre-gl` bundle measurement.
- `core_086` — baseline at which no in-tree projection adapter defaults
  to the `sensitive` privacy tier.

