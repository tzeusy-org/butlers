## MODIFIED Requirements

### Requirement: Needs Attention List

The home page SHALL render a `Needs attention` list composed from current state from
`GET /api/issues`, the canonical butler liveness verdict, pending approvals, bounded
notification delivery pressure, and active QA staffer state. A row SHALL represent either
live state or a time-bounded recent failure; older issue and notification records remain
available as history and SHALL NOT make the list or briefing imply that the system is
currently unhealthy. The list is a rule-separated attention surface, not a card grid or
table.

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
- **THEN** the full list is ordered by severity first -- critical, then high/error, then
  medium/warning/warn, then low, then all other severities -- across ALL kinds, not
  grouped by kind first
- **AND** a higher-severity row from one kind (for example, a tripped QA circuit breaker)
  SHALL rank above a lower-severity row from another kind (for example, a medium-severity
  issue), even if that other kind is normally rendered earlier
- **AND** rows tied on severity keep a stable, deterministic relative order (their kind's
  own internal ordering, for example issues by recency and approvals by soonest-expiry)
  so the list does not reshuffle between otherwise-identical renders
- **AND** the trailing `N more/older issue groups` rollup row and any explicitly included
  old-issue rows remain appended after the severity-sorted set, since they
  summarize/de-prioritize rather than represent a current signal

#### Scenario: A tripped QA circuit breaker surfaces as an attention row

- **WHEN** `GET /api/qa/summary`'s `circuit_breaker.tripped` is `true`
- **THEN** the attention list renders a critical-severity row naming the circuit breaker
  as tripped and the `consecutive_failures` count, linking to `/qa`
- **AND** this row takes precedence over a same-summary recent failed-patrol or active
  investigation row because a tripped breaker means the QA staffer has stopped
  dispatching entirely

#### Scenario: Active QA investigations surface as attention

- **WHEN** no QA breaker or recent failed patrol has higher precedence and
  `GET /api/qa/summary` reports `kpis.active_cases_now` greater than zero
- **THEN** the attention list renders a medium-severity row naming the active QA
  investigation count and linking to `/qa`
- **WHEN** only `stats_24h.dispatched_investigations` or `stats_24h.novel_findings` is
  greater than zero
- **THEN** the list does not render a QA attention row solely for that completed activity

#### Scenario: Notification pressure is time-bounded

- **WHEN** the Overview requests `GET /api/notifications/stats` with
  `since = now - 24 hours` and `until = now`, captured once for the render, and the
  bounded response has `failed` greater than zero
- **THEN** the list renders a medium-severity notification row naming the failed count in
  the last 24 hours
- **AND** its link preserves the `terminal_failed` status filter and both boundaries of
  that same closed interval, so it resolves the exact terminal-failure set counted by
  `NotificationStats.failed` rather than including attempts later superseded by a retry
- **AND** the Notifications destination renders that exact boundary in its visible local
  date-time filter rather than silently applying an undisclosed filter
- **WHEN** the bounded response has `failed = 0` while all-time failures exist
- **THEN** the list renders no normal notification-pressure row

#### Scenario: An unreachable notifications source surfaces as a degraded row

- **WHEN** `GET /api/notifications/stats` returns `source_available: false`
- **THEN** the attention list renders a high-severity, source-error row naming the
  notifications feed as unavailable, instead of silently showing no notification-pressure
  row (the underlying `failed` count is a fabricated zero in this case, not a genuine
  `no failures` result)
- **AND** this row does not also render alongside a normal `N failed notifications` row
  for the same fetch

#### Scenario: An active fleet-halt (monthly spend ceiling) surfaces as a critical row

- **WHEN** the fleet-halt status derived from `GET /api/dispatch/attempts` (see
  dashboard-spend-dashboard spec, Fleet-Halt Visibility) is active -- that is, the monthly
  spend ceiling has denied one or more dispatches this month
- **THEN** the attention list renders a critical-severity row reading `Monthly ceiling
  reached -- dispatches denied`, naming the denied-today count and the since-timestamp,
  linking to `/spend`
- **AND** this row ranks by the same severity-first ordering as every other attention row
  (critical sorts above high/medium/low)
- **AND** when the fleet-halt data source itself fails to load, the attention list renders
  a high-severity source-error row instead of silently omitting the fleet-halt signal
  (never reads a failed fetch as `the fleet is fine`)

#### Scenario: An unreachable butler board source surfaces as a degraded row

- **WHEN** `GET /api/butlers/board` fails to load (`butlersError` is `true`)
- **THEN** the attention list renders a high-severity, source-error row naming butler
  status as unavailable, linking to `/butlers`
- **AND** this holds even when no other attention source has a signal, so the list cannot
  silently render `Nothing waiting.` while the SAME board fetch drives the dashboard
  briefing headline's `"degraded"` state_class
  (`dashboard-briefing` spec's Degraded class scenario) -- the cross-surface consistency
  test pins this bound from a shared fixture

#### Scenario: Historical issues are summarized

- **WHEN** an unresolved issue's `last_seen_at` is older than 12 hours or is not
  parseable
- **THEN** the row is represented only by older-history detail or an aggregate rollup
- **AND** its age is calculated from `last_seen_at` relative to the owner's configured
  timezone
- **AND** repeated old issues with the same `type` and `description` MAY collapse into one
  summarized row when `occurrences` or `butlers` indicates multiplicity
- **AND** the summary MUST name the affected butlers with human-readable names, not raw
  machine identifiers

#### Scenario: Attention list handles empty, loading, and error states

- **WHEN** issues are loading
- **THEN** the list renders stable loading rows or an equivalent skeleton

- **WHEN** all loaded sources report no current attention rows
- **THEN** the list renders the serif Voice empty state `Nothing waiting.`
- **AND** it does not render an empty table, blank card, or celebratory graphic

- **WHEN** `GET /api/issues` fails
- **THEN** the list renders a local error row for the attention surface
- **AND** the rest of the Overview remains visible

### Requirement: Now List

The home page SHALL render a right-column `Now` section for immediate operational items.
In the first implementation this section is sourced from existing endpoints and does not
require a new endpoint. `Now` MAY show time-bounded completed activity, but that activity
SHALL NOT be treated as an active `Needs attention` signal or a briefing-classification
input.

The acceptable first-source set is:

- `GET /api/approvals/metrics` for pending approval count;
- `GET /api/qa/summary` for active QA cases and time-bounded patrol/finding/dispatch
  activity;
- `GET /api/qa/investigations` when the row needs active investigation or PR detail
  beyond the summary counts;
- `GET /api/notifications/stats` with the closed 24-hour `since` and `until` boundaries
  for failed notification pressure;
- `GET /api/timeline` for recent activity, or `GET /api/sessions` when the implementation
  only needs recent completed sessions.

#### Scenario: Pending approvals appear in Now

- **WHEN** `GET /api/approvals/metrics` returns `total_pending` greater than zero
- **THEN** `Now` renders one immediate item naming the pending approval count
- **AND** the item is labelled as an approval item

#### Scenario: QA state and activity appear in Now

- **WHEN** `GET /api/qa/summary` reports an active QA case, a recent patrol failure, or
  another current QA alert
- **THEN** `Now` renders an immediate item naming the QA state in human-readable terms
- **AND** if active investigation or PR detail is needed, the page MAY read
  `GET /api/qa/investigations` instead of introducing a new endpoint
- **WHEN** only `stats_24h.dispatched_investigations` or `stats_24h.novel_findings` is
  greater than zero
- **THEN** `Now` MAY render a compact activity item that names the count and its
  `last 24 hours` boundary
- **AND** it SHALL NOT label that completed activity as active follow-up work or failure

#### Scenario: Failed notification pressure appears in Now

- **WHEN** `GET /api/notifications/stats?since=<now-24h>&until=<now>` returns `failed`
  greater than zero
- **THEN** `Now` renders an immediate item naming the failed notification count in the
  last 24 hours
- **AND** the item is labelled as a notification item and links to the same
  `terminal_failed` window predicate

#### Scenario: Recent activity appears in Now

- **WHEN** `GET /api/timeline` returns recent activity, or `GET /api/sessions` returns
  recent completed sessions
- **THEN** `Now` MAY render a compact recent activity item
- **AND** the row links to the appropriate timeline or sessions surface when a link is
  available

#### Scenario: Now handles empty, loading, and error states

- **WHEN** one or more `Now` sources are loading
- **THEN** `Now` renders stable loading rows or an equivalent skeleton

- **WHEN** every loaded `Now` source reports no actionable state
- **THEN** `Now` renders `Nothing scheduled.`

- **WHEN** a `Now` source fails
- **THEN** `Now` renders a local error state for that source
- **AND** the rest of the Overview remains visible
