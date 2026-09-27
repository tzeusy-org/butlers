## MODIFIED Requirements

### Requirement: Needs Attention List

The home page SHALL render a `Needs attention` list composed from current state from
`GET /api/issues`, the canonical butler liveness verdict, pending approvals, bounded
notification delivery pressure, and active QA staffer state. A row SHALL represent either
live state or a time-bounded recent failure; older issue and notification records remain
available as history and SHALL NOT make the list or briefing imply that the system is
currently unhealthy. The list is a rule-separated attention surface, not a card grid or
table.

#### Scenario: Unknown latest QA patrol status surfaces as attention

- **WHEN** `GET /api/qa/summary` reports `staffer_status = "unknown_patrol_status"`
  for a latest completed patrol and its circuit breaker is not tripped
- **THEN** the attention list renders a high-severity `QA patrol status unknown` row
  linking to `/qa`
- **AND** the row explains that the latest patrol reported an unrecognized status
  without rendering the raw stored value as UI copy
- **AND** the same condition appears in the Overview's `Now` list and SHALL NOT be
  omitted as healthy, calm, or no QA attention
- **AND** a tripped breaker continues to take precedence over this row

#### Scenario: Attention rows are derived from current issues

- **WHEN** `GET /api/issues` returns one or more `Issue` objects whose parseable
  `last_seen_at` falls in the closed interval `[now - 12 hours, now]`
- **THEN** each current row shows severity mark, issue description, butler/source detail,
  optional error context, and a link when `link` is present
- **AND** within a severity tier, older unresolved current issues sort before newer issues
  when `first_seen_at` exists
- **WHEN** an issue has `last_seen_at` older than 12 hours or no parseable `last_seen_at`
- **THEN** it SHALL NOT render as a current attention row
- **AND** it remains eligible for the older-history rollup

#### Scenario: Attention rows are severity-first and stable across kinds

- **WHEN** the attention list composes rows from more than one source kind
  (issue, runtime/liveness, approval, notification, qa)
- **THEN** the full list is ordered by severity first — critical, then
  high/error, then medium/warning/warn, then low, then all other
  severities — across ALL kinds, not grouped by kind first
- **AND** a higher-severity row from one kind (e.g. a tripped QA circuit
  breaker) SHALL rank above a lower-severity row from another kind (e.g. a
  medium-severity issue), even if that other kind is normally rendered
  earlier
- **AND** rows tied on severity keep a stable, deterministic relative order
  (their kind's own internal ordering, e.g. issues by recency, approvals by
  soonest-expiry) so the list does not reshuffle between otherwise-identical
  renders
- **AND** the trailing "N more/older issue groups" rollup row and any
  explicitly-included old-issue rows remain appended after the severity-sorted
  set, since they summarize/de-prioritize rather than represent a current
  signal

#### Scenario: A tripped QA circuit breaker surfaces as an attention row

- **WHEN** `GET /api/qa/summary`'s `circuit_breaker.tripped` is `true`
- **THEN** the attention list renders a critical-severity row naming the
  circuit breaker as tripped and the `consecutive_failures` count, linking to
  `/qa`
- **AND** this row takes precedence over a same-summary recent patrol-error
  row (a tripped breaker means the QA staffer has stopped dispatching
  entirely, a more severe state than one failed patrol run)

#### Scenario: A recent QA patrol error surfaces as an attention row

- **WHEN** `GET /api/qa/summary` returns a `last_patrol` whose `status` is
  `error` and whose `started_at` is in the closed interval `[now - 24 hours,
  now]`, and its circuit breaker is not tripped
- **THEN** the attention list renders a high-severity "QA patrol failed" row
  linking to `/qa`
- **AND** a null `error_detail` still renders the failure row with generic
  failure context
- **AND** any non-`error` status, including one with non-null `error_detail`,
  does not render a patrol-failure row

#### Scenario: Active QA investigations surface as attention

- **WHEN** no QA breaker or recent patrol error has higher precedence and
  `GET /api/qa/summary` reports `kpis.active_cases_now` greater than zero
- **THEN** the attention list renders a medium-severity row naming the active QA
  investigation count and linking to `/qa`
- **WHEN** only `stats_24h.dispatched_investigations` or `stats_24h.novel_findings` is
  greater than zero
- **THEN** the list does not render a QA attention row solely for that completed activity

#### Scenario: Notification pressure is time-bounded

- **WHEN** the Overview requests `GET /api/notifications/stats` with a closed
  minute-aligned interval captured once for the render (`until` is the current
  minute boundary and `since = until - 24 hours`), and the bounded response has
  `failed` greater than zero
- **THEN** the list renders a medium-severity notification row naming the failed count in
  the last 24 hours
- **AND** its link preserves the `terminal_failed` status filter and both boundaries of
  that same closed interval, so it resolves the exact terminal-failure set counted by
  `NotificationStats.failed` rather than including attempts later superseded by a retry
- **AND** the Notifications destination renders that same minute-aligned boundary in its
  visible local date-time filter rather than silently applying an undisclosed filter
- **WHEN** the bounded response has `failed = 0` while all-time failures exist
- **THEN** the list renders no normal notification-pressure row

#### Scenario: An unreachable notifications source surfaces as a degraded row

- **WHEN** `GET /api/notifications/stats` returns `source_available: false`
- **THEN** the attention list renders a high-severity, source-error row
  naming the notifications feed as unavailable, instead of silently showing
  no notification-pressure row (the underlying `failed` count is a
  fabricated zero in this case, not a genuine "no failures" result)
- **AND** this row does not also render alongside a normal "N failed
  notifications" row for the same fetch

#### Scenario: An active fleet-halt (monthly spend ceiling) surfaces as a critical row

- **WHEN** the fleet-halt status derived from `GET /api/dispatch/attempts`
  (see dashboard-spend-dashboard spec, Fleet-Halt Visibility) is active — i.e.
  the monthly spend ceiling has denied one or more dispatches this month
- **THEN** the attention list renders a critical-severity row reading "Monthly
  ceiling reached — dispatches denied", naming the denied-today count and the
  since-timestamp, linking to `/spend`
- **AND** this row ranks by the same severity-first ordering as every other
  attention row (critical sorts above high/medium/low)
- **AND** when the fleet-halt data source itself fails to load, the attention
  list renders a high-severity source-error row instead of silently omitting
  the fleet-halt signal (never reads a failed fetch as "the fleet is fine")

#### Scenario: An unreachable butler board source surfaces as a degraded row

- **WHEN** `GET /api/butlers/board` fails to load (`butlersError` is `true`)
- **THEN** the attention list renders a high-severity, source-error row
  naming butler status as unavailable, linking to `/butlers`
- **AND** this holds even when no other attention source has a signal, so
  the list cannot silently render `Nothing waiting.` while the SAME board
  fetch drives the dashboard briefing headline's `"degraded"` state_class
  (`dashboard-briefing` spec's Degraded class scenario)

#### Scenario: Historical issues are summarized

- **WHEN** an unresolved issue's `last_seen_at` is older than 12 hours or is not
  parseable
- **THEN** the row is represented only by older-history detail or an aggregate rollup
- **AND** its age is calculated from `last_seen_at` relative to the owner's configured
  timezone
- **AND** repeated old issues with the same `type` and `description` MAY collapse
  into one summarized row when `occurrences` or `butlers` indicates multiplicity
- **AND** the summary MUST name the affected butlers with human-readable names,
  not raw machine identifiers

#### Scenario: Attention list handles empty, loading, and error states

- **WHEN** issues are loading
- **THEN** the list renders stable loading rows or an equivalent skeleton

- **WHEN** all loaded sources report no current attention rows
- **THEN** the list renders the serif Voice empty state `Nothing waiting.`
- **AND** it does not render an empty table, blank card, or celebratory graphic

- **WHEN** `GET /api/issues` fails
- **THEN** the list renders a local error row for the attention surface
- **AND** the rest of the Overview remains visible

### Requirement: Page Archetype Compliance

The home page SHALL adopt the Editorial archetype and render through the shared `<Page>`
primitive (`components/ui/page.tsx`). Layout, spacing, and conformance rules are owned by
`dashboard-design-language` (Requirement: Page Shell and Layout; Requirement: Page Conformance);
this requirement binds only the Overview's use of that archetype.

#### Scenario: Page renders inside the standard shell

- **WHEN** a user navigates to `/`
- **THEN** the home page SHALL render inside the standard dashboard shell
  (sidebar, header bar, error boundary) as defined by `dashboard-shell`
- **AND** the page content SHALL not reimplement chrome that belongs to the shell

#### Scenario: Page uses the shared Page primitive

- **WHEN** `DashboardPage` renders
- **THEN** it SHALL use `<Page archetype="editorial" title="Overview">` as its
  outermost container
- **AND** the cockpit surfaces SHALL be direct children of `<Page>` rather than an
  extra layout wrapper

## REMOVED Requirements

### Requirement: Runtime KPI Strip

**Reason**: Renamed so the door scenario no longer carries a tracker id in its name.

**Migration**: See Requirement: Runtime KPI Strip Cells and Doors, which carries the same
contract unchanged.

## ADDED Requirements

### Requirement: Runtime KPI Strip Cells and Doors

The home page SHALL render a promoted four-cell runtime KPI strip. "Promoted"
means the KPIs are part of the primary information hierarchy; it does not mean
they use heavier card chrome. The strip SHALL remain hairline-divided,
tabular-numeric, and visually calm.

#### Scenario: KPI cells have defined meanings

- **WHEN** the runtime KPI strip renders
- **THEN** it includes exactly these four cells:
  - `Total butlers`: count of `GET /api/butlers` rows where `type` is `"butler"`
  - `Healthy`: count of butler rows whose `status` is `"ok"`, `"online"`, or `"healthy"`
  - `Sessions · 24h`: sum of `sessions_24h` across butler rows
  - `Pending approvals`: `total_pending` from `GET /api/approvals/metrics`
- **AND** every numeric value uses tabular numerals

#### Scenario: KPI strip handles loading and partial failure

- **WHEN** either KPI source is still loading
- **THEN** cells depending on unavailable data render an unavailable/loading
  value without shifting layout

- **WHEN** one KPI source fails
- **THEN** cells backed by the failed source render an unavailable/error value
- **AND** cells backed by the still-available source MAY continue rendering

#### Scenario: KPI cells are doors to supported destinations only

- **WHEN** a KPI cell's backing value is available (including a genuine zero)
- **THEN** the whole cell is a navigable door: `Total butlers` routes to
  `/butlers`; `Healthy` routes to the SAME unfiltered `/butlers` board (no
  `healthy`-only filter exists anywhere in the product) with an accessible
  name that says so explicitly; `Sessions · 24h` routes to
  `/sessions?since=<captured-since>&until=<captured-until>` using one 24-hour
  window captured once per render, not a fresh instant recomputed between
  render and click; `Pending approvals` routes to `/approvals`

#### Scenario: Unavailable KPI cells never carry a door

- **WHEN** a KPI cell renders its unavailable value (`—`, from loading, error,
  or a degraded source)
- **THEN** that cell has no href and is not a link, div[role=link], button, or
  any other interactive control
- **AND** a genuine zero value is unaffected by this rule and keeps its door

#### Scenario: Degraded Sessions aggregate leaves the other KPI doors available

- **WHEN** `GET /api/butlers/board` succeeds but reports
  `aggregates.sessions_source_error = true`
- **THEN** `Sessions · 24h` renders `—` with unavailable semantics and no
  Sessions door, because its aggregate is only a partial sum
- **AND** the available `Total butlers`, `Healthy`, and `Pending approvals`
  values retain their normal values and supported doors


### Requirement: Spend widget for dashboard overview

The Overview MUST render a full-width cost band below the editorial grid: a `CostWidget` in a
half-width column followed by the `TopSessionsTable`. The widget MUST display:
- Title "Cost Today" with a "View all" link to `/spend`.
- Total cost for the day formatted as currency when its direct summary query succeeds with priced data.
- Top butler name and cost (e.g., "Top: health ($3.50)") when its direct summary query succeeds with a top butler.
- A sparkline showing the real trailing 7-day daily spend series.

The widget MUST distinguish a direct Overview summary-query failure from a successful
compatibility envelope with `source_error` and from a successful zero-cost summary.

#### Scenario: Widget with no data

- **WHEN** `totalCostUsd` is 0 and `topButler` is null
- **AND** the direct summary query succeeded without `source_error`
- **THEN** the widget MUST display "$0.00" and no top-butler line

#### Scenario: Direct summary reader failure is unavailable

- **WHEN** the Overview's direct `useSpendSummary("today")` query reports an error
- **THEN** `DashboardPage` MUST pass an explicit unavailable state to `CostWidget`
- **AND** the widget MUST render a named cost-summary-unavailable state
- **AND** it MUST NOT render a formatted cost total or a top-butler claim from fallback or retained data

#### Scenario: Successful compatibility summary remains degraded

- **WHEN** the direct summary request succeeds with `source_error: true`
- **THEN** the widget MUST render its existing source-degraded state
- **AND** it MUST NOT render the direct-summary-unavailable state or a calm "$0.00" total

#### Scenario: Cost band renders below the cockpit

- **WHEN** a user navigates to `/`
- **THEN** the cost band renders below the editorial grid, with `CostWidget` above
  `TopSessionsTable`
- **AND** the widget draws from the direct `useSpendSummary("today")` query and the table from
  the direct `useTopSessions()` query

### Requirement: Top sessions table

The Overview cost band MUST provide a `TopSessionsTable` displaying the most expensive LLM sessions. The table MUST display columns: rank number (#), Butler (secondary badge), Model (muted text), Tokens (input/output formatted as abbreviated counts separated by "/"), Cost (right-aligned, bold, tabular-nums), Time (right-aligned, formatted as "MMM d, HH:mm").

#### Scenario: Session token display

- **WHEN** a session has 50,000 input tokens and 12,000 output tokens
- **THEN** the Tokens column MUST display "50.0K / 12.0K"

#### Scenario: Direct top-sessions reader failure is unavailable

- **WHEN** the Overview's direct `useTopSessions()` query reports an error
- **THEN** `DashboardPage` MUST pass an explicit unavailable state to `TopSessionsTable`
- **AND** the table MUST render a named top-sessions-unavailable state before its empty-state branch
- **AND** it MUST NOT render "No session data available"

#### Scenario: Successful empty top sessions remain calm

- **WHEN** the direct top-sessions query succeeds with an empty list
- **THEN** the table MUST render "No session data available"
- **AND** it MUST NOT render the top-sessions-unavailable state
