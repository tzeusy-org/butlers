## MODIFIED Requirements

### Requirement: Fleet-Halt Visibility

The dashboard SHALL surface the monthly spend ceiling's enforcement action —
dispatches being denied fleet-wide — as a loud, explicit state on the Spend
page, not silence. The ceiling is enforced by `check_monthly_ceiling` / the
spawner, which writes an `outcome='quota_skip'` row to
`public.model_dispatch_attempts` with `failure_reason` starting `Monthly spend
ceiling reached` for every denied dispatch.

ID: REQ-dashboard-spend-dashboard-001
Source: dashboard-spend-dashboard Fleet-Halt Visibility; runtime-attention-outbox REQ-runtime-attention-outbox-001; design.md Decisions 3-5
Scope: v1-mandatory

#### Scenario: A red fleet-halt banner renders while the ceiling is breached

- **WHEN** `GET /api/dispatch/attempts?outcome=quota_skip&reason_prefix=Monthly+spend+ceiling+reached`
  (scoped to the current calendar month) returns one or more rows
- **THEN** the Spend page renders a red state reading `Monthly ceiling reached —
  N dispatches denied since <timestamp>`, where N is the total matching count
  for the current month and `<timestamp>` is the earliest matching row's `ts`
- **AND** the banner additionally shows a denied-today count (rows since the
  start of the current owner-tz day)
- **AND** the banner does not render when no such rows exist for the current
  month

#### Scenario: An attempts drawer lists recent denials with session doors

- **WHEN** the fleet-halt banner is active
- **THEN** an expandable drawer lists the most recent denied attempts (butler,
  timestamp, failure reason)
- **AND** each row whose `session_id` is non-null links to that session's
  detail page (`/sessions/:id`)
- **AND** rows with no `session_id` render without a session door instead of a
  dead or broken link

#### Scenario: The owner is notified exactly once per breach window

- **WHEN** the monthly ceiling transitions from not-breached to breached — the
  first current-calendar-month `quota_skip` dispatch denial whose
  `failure_reason` starts `Monthly spend ceiling reached`
- **THEN** the authorized producer operation in the spawner transaction appends
  exactly one `fleet_halt` attention episode keyed to that calendar-month breach
  window and carrying only the safe denied-dispatch count and Spend drawer door
- **AND** that episode is the sole route by which the owner is notified — the
  spawner reaches Switchboard's outbox delivery and nothing else, so it neither
  pages the owner directly nor relies on an audit-log debounce marker
- **AND** every later denial in that same breach window appends neither another
  episode nor another page

#### Scenario: Switchboard delivery is failure-isolated from a ceiling denial

- **WHEN** the fleet-halt episode is pending, sent, failed, or uncertain
- **THEN** Switchboard owns its delivery lifecycle under the
  `runtime-attention-outbox` requirements
- **AND** an episode claim, Messenger failure, or bookkeeping failure never
  blocks or delays the spawner's ceiling-deny decision

#### Scenario: Degraded attempts source never renders as "no denials"

- **WHEN** `GET /api/dispatch/attempts` fails (network error, non-2xx)
- **THEN** the Spend page SHALL render a degraded-source note for the fleet-halt
  state instead of silently omitting the banner
