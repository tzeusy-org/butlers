## MODIFIED Requirements

### Requirement: Attention Item Sources

The endpoint SHALL populate `state.attention_items` from five sources before classification:
butler liveness, grouped error entries from the `dashboard_audit_log` table, pending
approvals, failed notifications, and QA state. An attention item SHALL represent either a
live state or a time-bounded recent failure; historical aggregates SHALL remain outside
`state.attention_items` and SHALL NOT affect briefing classification, headline, or
elaboration. Each source is fetched independently and concurrently; a failure in one
source MUST NOT prevent the others from contributing.

#### Scenario: Board-derived attention items (butler liveness)

- **WHEN** `GET /api/butlers/board`'s canonical per-row `activity` verdict for a
  `"butler"`-type row is one of `"offline"`, `"quarantined"`, or `"overdue"`
- **THEN** that row is added to `state.attention_items` as a single attention item
- **AND** `"offline"` and `"quarantined"` carry `severity = "high"`; `"overdue"`
  carries `severity = "medium"`
- **AND** `source` is `"board"`
- **WHEN** a row's activity is `"unknown"` (that butler's own heartbeat/schema is
  unreachable, or its registry `last_seen_at` is clock-skewed more than 5 minutes into
  the future) and the board's registry query itself did not fail
- **THEN** that row is likewise added with `severity = "medium"`
- **WHEN** the board's registry query itself failed, uniformly degrading every row's
  activity to `"unknown"`
- **THEN** no attention item is fabricated per butler from that systemic outage
- **AND** the failure is instead tracked as the `"board"` degraded source (see the
  degraded-sources scenario below)

#### Scenario: Audit-derived attention items

- **WHEN** a grouped `dashboard_audit_log` error has a parseable `last_seen_at` in the
  closed interval `[now - 12 hours, now]`
- **THEN** it is appended to `state.attention_items` using its first-line error summary
- **AND** it receives `severity = "high"` when any row in the group originated from a
  scheduled session (`trigger_source` starts with `"schedule:"`)
- **AND** it receives `severity = "medium"` when none of the rows in the group were
  schedule-triggered
- **AND** `source` is `"audit_log"`
- **WHEN** a grouped audit error was last seen more than 12 hours ago, or lacks a
  parseable `last_seen_at`
- **THEN** it is historical context and SHALL NOT be appended to
  `state.attention_items`

This means a recurring scheduled-task failure raises `state_class` to `"urgent"` only
while it is within the current operational horizon. Ad-hoc errors that do not originate
from a schedule are surfaced as `"medium"` while current, so they contribute to
`"busy"` or `"mild"` without forcing `"urgent"`.

#### Scenario: Approvals-derived attention item

- **WHEN** one or more pending approvals exist across any butler's `pending_actions`
  table
- **THEN** a single attention item is added with `severity = "medium"` naming the total
  pending count
- **AND** `source` is `"approval"`

#### Scenario: Notification-derived attention item

- **WHEN** briefing composition requests `GET /api/notifications/stats` with
  `since = now - 24 hours` and `until = now`, captured once for the composition, and that
  bounded response has `failed` greater than zero
- **THEN** a single attention item is added with `severity = "medium"` naming the failed
  count in the last 24 hours
- **AND** `source` is `"notification"`
- **WHEN** the same bounded response has `failed = 0` while an all-time notification
  total is greater than zero
- **THEN** no notification attention item is added

Only recent failed deliveries are a genuine attention-worthy signal. Lifetime delivery
totals remain available on the Notifications page and SHALL NOT affect briefing state.

#### Scenario: QA-derived attention item

- **WHEN** `GET /api/qa/summary`'s circuit breaker (computed from
  `public.healing_attempts` the same way `qa.py`'s `/api/qa/circuit-breaker` and
  dispatch-admission gate do) is tripped
- **THEN** a single attention item is added with `severity = "high"` and `source =
  "qa"` naming the tripped breaker and its consecutive-failure count
- **AND** no further QA checks are considered because a tripped breaker means the QA
  staffer has stopped dispatching entirely
- **WHEN** the breaker is not tripped, and the most recent non-running QA patrol failed
  or has non-null `error_detail` in the closed interval `[now - 24 hours, now]`
- **THEN** a single attention item is added with `severity = "high"` and `source =
  "qa"`, and no further QA checks are considered
- **WHEN** neither higher-precedence state applies and
  `GET /api/qa/summary` reports `kpis.active_cases_now` greater than zero
- **THEN** a single attention item is added with `severity = "medium"` and `source =
  "qa"` naming the active investigation count
- **WHEN** neither higher-precedence state applies and only
  `stats_24h.dispatched_investigations` or `stats_24h.novel_findings` is greater than
  zero
- **THEN** no QA attention item is added because completed dispatches and findings are
  time-bounded activity rather than active failure state
- **WHEN** the QA tables are not provisioned on this deployment (undefined relation)
- **THEN** QA is treated as legitimately absent, contributing no attention item and no
  degraded source
- **WHEN** the `public.qa_patrols` query succeeds but the circuit-breaker query against
  `public.healing_attempts` fails for a reason other than the table being un-provisioned
- **THEN** the QA source is recorded in `state.degraded_sources`, but the patrol-derived
  signal already fetched still contributes normally -- one query's failure does not
  discard another query's successful result

#### Scenario: Attention item source fetch failure

- **WHEN** any of the five source fetches (board, audit, approvals, notifications, QA)
  fails with an exception, or a source explicitly reports itself unreachable (for example,
  `NotificationStats.source_available = false`)
- **THEN** that source's items are omitted from `state.attention_items`
- **AND** the endpoint logs a WARNING and continues with the remaining sources
- **AND** the source's name is recorded in `state.degraded_sources`
- **AND** `state_class` is computed from whatever items were successfully retrieved, per
  the Degraded class scenario below

A source that is legitimately absent (an un-migrated table on a deployment that has not
provisioned that module) is NOT recorded as degraded -- only a genuine failure (dropped
connection, timeout, permission error) is.

### Requirement: Per-Owner Caching

The endpoint SHALL cache the Briefing per owner contact for 5 minutes.

#### Scenario: Cache hit

- **WHEN** an owner calls the endpoint within 5 minutes of a prior successful call
- **THEN** the response is served from cache
- **AND** `generated_at` reflects the original cached generation time, not the current
  time

#### Scenario: Cache miss after TTL

- **WHEN** more than 5 minutes have elapsed since the last cached Briefing for the owner
- **THEN** a fresh Briefing is composed
- **AND** the cache is repopulated
- **AND** `generated_at` reflects the new generation time

#### Scenario: Successful QA breaker reset invalidates cached briefings

- **WHEN** `POST /api/qa/circuit-breaker/reset` successfully commits its reset marker
- **THEN** the in-process briefing cache is invalidated before the route returns success
- **AND** the next owner briefing request composes current state without waiting for the
  five-minute TTL
- **AND** a briefing composition that began before the reset cannot repopulate the cache
  after that invalidation, even if its own response completes later
- **WHEN** the reset request finds no tripped breaker or the reset marker write fails
- **THEN** cached briefings remain intact
