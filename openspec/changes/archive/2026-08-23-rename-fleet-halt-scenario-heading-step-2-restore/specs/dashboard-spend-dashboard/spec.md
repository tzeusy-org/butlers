## ADDED Requirements

### Requirement: Fleet-Halt Visibility
The dashboard SHALL surface the monthly spend ceiling's enforcement action —
dispatches being denied fleet-wide — as a loud, explicit state on the Spend page,
not silence. The ceiling is enforced by `check_monthly_ceiling` / the spawner
(`spawner.py:1179-1202`), which writes an `outcome='quota_skip'` row to
`public.model_dispatch_attempts` with `failure_reason` starting `"Monthly spend
ceiling reached"` for every denied dispatch (see model-failover spec, Failover
Attempt Provenance).

#### Scenario: A red fleet-halt banner renders while the ceiling is breached
- **WHEN** `GET /api/dispatch/attempts?outcome=quota_skip&reason_prefix=Monthly+spend+ceiling+reached`
  (scoped to the current calendar month) returns one or more rows
- **THEN** the Spend page renders a red state reading "Monthly ceiling reached —
  N dispatches denied since `<timestamp>`", where N is the total matching count
  for the current month and `<timestamp>` is the earliest matching row's `ts`
- **AND** the banner additionally shows a denied-today count (rows since the
  start of the current owner-tz day)
- **AND** the banner does not render when no such rows exist for the current month

#### Scenario: An attempts drawer lists recent denials with session doors
- **WHEN** the fleet-halt banner is active
- **THEN** an expandable drawer lists the most recent denied attempts (butler,
  timestamp, failure reason)
- **AND** each row whose `session_id` is non-null links to that session's detail
  page (`/sessions/:id`), mirroring the session-door pattern the Top Sessions
  table already uses
- **AND** rows with no `session_id` (pre-session ceiling denials) render without
  a session door instead of a dead or broken link

#### Scenario: The owner is notified exactly once per breach window
- **WHEN** the monthly ceiling transitions from not-breached to breached — the
  first `quota_skip` dispatch denial with `failure_reason` prefix `"Monthly
  spend ceiling reached"` in the current calendar month, detected inline in the
  spawner's ceiling-deny branch (`spawner.py`, `maybe_push_fleet_halt_attention`
  in `butlers.core.fleet_halt_attention`)
- **THEN** exactly one `public.attention_ledger` row is written with
  `source="notify"`, `outcome="delivered"`, `priority_label="high"` (the
  same lever `notify()` itself uses to always bypass quiet-hours/context-bus
  suppression — a fleet halt is expressed via the ledger's own severity dial,
  not a bespoke bypass), a `dedup_key` identifying the calendar-month halt
  window (e.g. `ceiling_halt:2026-07`), and `metadata` carrying the current
  denied-dispatch count for the month plus a door URL into the Spend page's
  attempts drawer (`/spend?openDrawer=fleet-halt`, which auto-expands the
  drawer from Scenario "An attempts drawer lists recent denials with session
  doors" above)
- **AND** the owner is paged through the same notify-boundary gating/dispatch
  primitives `notify()` uses (quiet hours via `public.approvals_policy`,
  context-bus dnd/sleeping, Switchboard `deliver()`)
- **AND** every subsequent ceiling denial in the same calendar month writes
  NEITHER another `attention_ledger` row NOR another page — debounced by a
  durable per-window marker in `public.audit_log`
  (`action="ceiling_halt_notified"`, `note=<the window>`), mirroring the same
  debounce mechanism `butlers.jobs.secrets_lifecycle` already uses for its own
  once-per-state-transition owner pushes
- **AND** the entire push is best-effort and failure-isolated: any failure
  (ledger write, debounce lookup, delivery) is logged and swallowed, and never
  blocks or delays the spawner's deny decision

#### Scenario: Degraded attempts source never renders as "no denials"
- **WHEN** `GET /api/dispatch/attempts` fails (network error, non-2xx)
- **THEN** the Spend page SHALL render a degraded-source note for the fleet-halt
  state (per the fleet degraded-source convention) instead of silently omitting
  the banner, which would read as a false "the fleet is not halted"
