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
  (`dashboard-briefing` spec's Degraded class scenario) -- bu-gcz9e.2's
  cross-surface consistency test pins this bound from a shared fixture

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
